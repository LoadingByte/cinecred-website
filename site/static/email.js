function revealEmail(a, encoded) {
    const href = Array.from(encoded).map((c) => String.fromCodePoint(c.codePointAt(0) + 11)).join("")
    a.removeAttribute("onclick")
    a.setAttribute("href", href)
    a.lastChild.textContent = href.substring(7)
}
