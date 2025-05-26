import bz2
import gzip
import json
import lzma
import re
import subprocess
from collections import namedtuple
from email.utils import formatdate
from functools import partial
from hashlib import md5, sha1, sha256
from pathlib import Path
from shutil import rmtree
from sys import stderr

import createrepo_c
from jinja2 import Environment, FileSystemLoader

LANGS = ["cs", "de", "en", "es", "fr", "zh-CN"]
FALLBACK_LANG = "en"

repo_dirpath = Path(__file__).parent
site_dirpath = repo_dirpath / "site"
build_dirpath = repo_dirpath / "build"

if build_dirpath.exists():
    rmtree(build_dirpath)

# Parse the localization dictionaries.
translations = {lang: {} for lang in LANGS}
for lang in LANGS:
    filename_suffix = ("" if lang == FALLBACK_LANG else ("_" + lang.replace("-", "_"))) + ".properties"
    for dirpath, prefix in [
        (repo_dirpath / "l10n" / "strings", ""),
        (repo_dirpath / "cinecred" / "src" / "main" / "resources" / "l10n" / "strings", "cinecred."),
        (repo_dirpath / "cinecred" / "src" / "demo" / "resources" / "l10n" / "demo", "demo.")
    ]:
        with open(dirpath.parent / (dirpath.name + filename_suffix), "r") as f:
            for line in f.readlines():
                key, value = line.strip().split("=", 1)
                translations[lang][prefix + key] = value
l10n = lambda lang, k: translations[lang].get(k, translations[FALLBACK_LANG][k]).replace(r"\n", "<br>")

# Validate and hardlink static files.
for dirpath, _, filenames in site_dirpath.walk():
    rel_dirpath = dirpath.relative_to(site_dirpath)
    (out_dirpath := build_dirpath / rel_dirpath).mkdir(parents=True, exist_ok=True)
    groups = {}
    for filename in filenames:
        if not filename.endswith(".html"):
            *stem, ext = filename.rsplit(".", 2)
            if len(stem) == 2 and stem[1] in LANGS:
                groups.setdefault((stem[0], ext), []).append(stem[1])
            else:
                groups.setdefault((".".join(stem), ext), []).append(None)
    for (stem, ext), langs in groups.items():
        if None in langs:
            filename = f"{stem}.{ext}"
            if len(langs) != 1:
                raise ValueError(f"The non-localized file '{rel_dirpath / filename}' is mixed with localized files.")
            (out_dirpath / filename).hardlink_to(dirpath / filename)
        else:
            if len(langs) != len(LANGS):
                print(f"The localized files '{rel_dirpath / f"{stem}.<LANG>.{ext}"}' miss languages.", file=stderr)
            for lang in LANGS:
                src_lang = lang if lang in langs else FALLBACK_LANG
                (out_dirpath / f"{stem}.{lang}.{ext}").hardlink_to(dirpath / f"{stem}.{src_lang}.{ext}")

# Locate HTML files and retrieve their titles.
Page = namedtuple("Page", ["rel_filepath", "url", "title_key"])
pages = []
for dirpath, _, filenames in site_dirpath.walk():
    rel_dirpath = dirpath.relative_to(site_dirpath)
    for filename in filenames:
        if filename.endswith(".html") and not filename.startswith("_"):
            url = rel_dirpath
            if filename != "index.html":
                url /= filename[:-5]
            title_key = ("home" if url == Path() else str(url).replace("/", ".")) + ".title"
            pages.append(Page(rel_dirpath / filename, url, title_key))
root_page = next(page for page in pages if page.url == Path())

# Locate releases and their downloadable assets.
Release = namedtuple("Release", ["version", "assets"])
Asset = namedtuple("Asset", ["url", "platform", "kind"])
platforms = ["Windows", "macOS Intel", "macOS ARM", "Linux"]
releases = []
for dirpath in filter(Path.is_dir, (site_dirpath / "dl").iterdir()):
    assets = []
    for filepath in dirpath.iterdir():
        url = filepath.relative_to(site_dirpath)
        n, s = filepath.name.rsplit(".", 1)
        pl_idx = 0 if "windows" in n or s == "msi" else (1 if "x86_64" in n else 2) if "mac" in n or s == "pkg" else 3
        kind = {"appimage": "AppImage", "gz": "TAR.GZ"}.get(s, s.upper())
        assets.append(Asset(url, platforms[pl_idx], kind))
    assets.sort(key=lambda asset: (platforms.index(asset.platform), asset.kind))
    releases.append(Release(dirpath.name, assets))
releases.sort(key=lambda release: list(map(int, release.version.split("."))), reverse=True)

# Prefix the titles of guide pages with numbers, ordered as they appear in the localization dictionary.
guide_no = []
for page in sorted(pages, key=lambda page: list(translations[FALLBACK_LANG]).index(page.title_key)):
    if len(page.url.parts) >= 2 and page.url.parts[0] == "guide":
        guide_no = (guide_no + [0])[:len(page.url.parts) - 1]
        guide_no[-1] += 1
        for t in translations.values():
            if page.title_key in t:
                t[page.title_key] = ".".join(map(str, guide_no)) + ". " + t[page.title_key]


# For each found HTML file, generate one HTML file per language.

def sitemap(page, selected_page, lang):
    if page.url.name == "404":
        return ""
    html = "<li>"
    if page is selected_page:
        html += f'<strong>{l10n(lang, page.title_key)}</strong>'
    else:
        html += f'<a href="/{page.url}/">{l10n(lang, page.title_key)}</a>'
    child_pages = [child for child in pages if child.url != Path() and child.url.parent == page.url]
    if child_pages:
        child_pages.sort(key=lambda child: re.sub(r"^(\d\.)", r"0\1", l10n(lang, child.title_key)))
        html += "<ul>" + "".join(sitemap(child_page, selected_page, lang) for child_page in child_pages) + "</ul>"
    html += "</li>"
    if page.url == Path():
        html = f"<ul>{html}</ul>"
    return html


def toc(h_ids, h_titles, lang):
    if not h_ids:
        return ""
    html = f'<ul><li><a href="#">{l10n(lang, "top")}</a>'
    last_level = 2
    for h_id, (h_level, h_title) in zip(h_ids, h_titles):
        if h_level == last_level:
            html += "</li>"
        elif h_level > last_level:
            html += "<ul>" + "<li><ul>" * (h_level - last_level - 1)
        elif h_level < last_level:
            html += "</li></ul>" * (last_level - h_level) + "</li>"
        last_level = h_level
        html += f'<li><a href="#{h_id}">{h_title}</a>'
    while last_level >= 2:
        last_level -= 1
        html += "</li></ul>"
    return html


def h_repl(h_ids, h_titles, m):
    if len(h_ids) == len(h_titles):
        h_ids.append(m[3] or re.sub(r"\W+", "-", m[4].lower()).strip("-"))
    h_titles.append((int(m[1]), m[4]))
    return f'<h{m[1]} id="{h_ids[len(h_titles) - 1]}">{m[4]}</h{m[1]}>'


jinja_env = Environment(
    trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True, loader=FileSystemLoader(site_dirpath)
)
for page in pages:
    (out_dirpath := build_dirpath / page.url).mkdir(parents=True, exist_ok=True)
    jinja_template = jinja_env.get_template(str(page.rel_filepath))
    h_ids = []
    for lang in sorted(LANGS, key=lambda lang: 0 if lang == FALLBACK_LANG else 1):
        breadcrumb = sorted([
            (parent.url, l10n(lang, parent.title_key)) for parent in pages
            if parent is not page and page.url.is_relative_to(parent.url)
        ], key=lambda t: len(t[0].parts))
        html = jinja_template.render(
            avail_langs=LANGS, lang=lang, title=l10n(lang, page.title_key),
            sitemap=sitemap(root_page, page, lang), breadcrumb=breadcrumb, platforms=platforms, releases=releases,
            l10n=lambda *args: l10n(lang, args[0]) if len(args) == 1 else l10n(*args),
            choose_transl=lambda d: d.get(lang, d[FALLBACK_LANG])
        )
        if "[[TOC]]" in html:
            h_titles = []
            html = re.sub('<h([2-6])( id="(.+)")?>(.+)</h[2-6]>', partial(h_repl, h_ids, h_titles), html)
            html = html.replace("[[TOC]]", toc(h_ids, h_titles, lang), 1)
        with open(out_dirpath / f"index.{lang}.html", "w") as f:
            f.write(html)

# Generate the update checker's JSON file.
(json_dirpath := build_dirpath / "dl" / "api" / "v1").mkdir(parents=True)
with open(json_dirpath / "components", "w") as f:
    json.dump({"components": [{"qualifier": "Release", "version": releases[0].version}]}, f)

# Generate the apt repository.
apt_dirpath = build_dirpath / "debian"
(apt_pool_dirpath := apt_dirpath / "pool" / "main").mkdir(parents=True)
for release in releases:
    asset = next(filter(lambda asset: asset.url.suffix == ".deb", release.assets))
    (apt_pool_dirpath / f"cinecred_{release.version}-1_amd64.deb").symlink_to(Path("..") / ".." / ".." / asset.url)
apt_rl_dirpath = apt_dirpath / "dists" / "stable"
(apt_pkg_dirpath := apt_rl_dirpath / "main" / "binary-amd64").mkdir(parents=True)
with open(apt_pkg_dirpath / "Packages", "wb+") as f:
    subprocess.run(["dpkg-scanpackages", "--multiversion", "pool/"], stdout=f, cwd=apt_dirpath)
    f.seek(0)
    pkg = f.read()
pkgs = [("", pkg), (".xz", lzma.compress(pkg)), (".gz", gzip.compress(pkg)), (".bz2", bz2.compress(pkg))]
for suffix, pkg in pkgs:
    with open(apt_pkg_dirpath / f"Packages{suffix}", "wb") as f:
        f.write(pkg)
rl = f"Label: Cinecred\nSuite: stable\nCodename: stable\nComponents: main\nArchitectures: amd64\nDate: {formatdate()}\n"
for label, fn in [("SHA256", sha256), ("SHA1", sha1), ("MD5Sum", md5)]:
    rl += label + ":\n"
    for suffix, pkg in pkgs:
        rl += f" {fn(pkg).hexdigest()} {len(pkg)} main/binary-amd64/Packages{suffix}\n"
rl = rl.encode()
with open(apt_rl_dirpath / "Release", "wb") as f:
    f.write(rl)
with open(apt_rl_dirpath / "Release.gpg", "wb") as f:
    subprocess.run(["gpg", "-abu", "cinecred.com"], input=rl, stdout=f)
with open(apt_rl_dirpath / "InRelease", "wb") as f:
    subprocess.run(["gpg", "-abu", "cinecred.com", "--clearsign"], input=rl, stdout=f)

# Generate the yum repository.
yum_dirpath = build_dirpath / "yum"
(yum_pkg_dirpath := yum_dirpath / "packages").mkdir(parents=True)
for release in releases:
    asset = next(filter(lambda asset: asset.url.suffix == ".rpm", release.assets))
    (yum_pkg_dirpath / f"cinecred-{release.version}-1.x86_64.rpm").symlink_to(Path("..") / ".." / asset.url)
createrepo_c._program("createrepo_c", [yum_dirpath])
subprocess.run(["gpg", "-abu", "cinecred.com", yum_dirpath / "repodata" / "repomd.xml"])
