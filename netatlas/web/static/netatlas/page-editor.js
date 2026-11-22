// The `@` Mention picker of the Page editor. Without JavaScript the textarea still works.
(() => {
  const textarea = document.querySelector("textarea[data-mention-url]");
  const list = document.getElementById("mention-options");
  if (!textarea || !list) return;

  let active = -1;
  let timer = null;
  let request = 0;

  const options = () => [...list.querySelectorAll("[role=option]")];

  // The `@word` being typed at the caret, if any.
  const typed = () => {
    const before = textarea.value.slice(0, textarea.selectionStart);
    const match = before.match(/(?:^|\s)@([^\s@[\]]*)$/);
    return match ? { query: match[1], start: before.length - match[1].length - 1 } : null;
  };

  const close = () => {
    request += 1;
    list.hidden = true;
    list.replaceChildren();
    active = -1;
    textarea.setAttribute("aria-expanded", "false");
    textarea.removeAttribute("aria-activedescendant");
  };

  const highlight = (index) => {
    const all = options();
    active = (index + all.length) % all.length;
    all.forEach((option, i) => option.setAttribute("aria-selected", String(i === active)));
    textarea.setAttribute("aria-activedescendant", all[active].id);
    all[active].scrollIntoView({ block: "nearest" });
  };

  const choose = (option) => {
    const found = typed();
    if (!found) return close();
    const token = `${option.dataset.token} `;
    const end = textarea.selectionStart;
    textarea.setRangeText(token, found.start, end, "end");
    close();
    textarea.focus();
  };

  const search = async (query) => {
    const current = ++request;
    const url = `${textarea.dataset.mentionUrl}?q=${encodeURIComponent(query)}`;
    try {
      const response = await fetch(url, { headers: { Accept: "text/html" } });
      if (!response.ok || current !== request) return;
      // The server renders and escapes this fragment.
      list.innerHTML = await response.text();
    } catch {
      if (current === request) close();
      return;
    }
    if (current !== request) return;
    const hasOptions = options().length > 0;
    list.hidden = !hasOptions;
    textarea.setAttribute("aria-expanded", String(hasOptions));
    if (hasOptions) highlight(0);
  };

  textarea.addEventListener("input", () => {
    clearTimeout(timer);
    const found = typed();
    if (!found) return close();
    timer = setTimeout(() => search(found.query), 150);
  });

  textarea.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      highlight(active + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      choose(options()[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });

  // Choose on mousedown, before the textarea loses focus and its caret.
  list.addEventListener("mousedown", (event) => {
    const option = event.target.closest("[role=option]");
    if (option) {
      event.preventDefault();
      choose(option);
    }
  });

  textarea.addEventListener("blur", close);
})();
