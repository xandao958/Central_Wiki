(() => {
  const themeButtons = document.querySelectorAll("[data-theme-toggle]");
  const articleForm = document.querySelector("[data-article-form]");
  const mobileActions = document.querySelector(".mobile-actions");
  const horizontalScrollers = new Map();
  let scanScheduled = false;

  function saveInlineStyle(element, property) {
    return {
      value: element.style.getPropertyValue(property),
      priority: element.style.getPropertyPriority(property),
    };
  }

  function restoreInlineStyle(element, property, saved) {
    if (saved.value) element.style.setProperty(property, saved.value, saved.priority);
    else element.style.removeProperty(property);
  }

  function isVisible(element) {
    if (!element.getClientRects().length || element.closest("[hidden], [aria-hidden='true']")) return false;
    for (let current = element; current; current = current.parentElement) {
      const style = window.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse"
        || Number(style.opacity) === 0) {
        return false;
      }
    }
    return true;
  }

  function updateScrollCues(element, cues) {
    const maxScroll = element.scrollWidth - element.clientWidth;
    const bounds = element.getBoundingClientRect();
    const inViewport = bounds.bottom > 0 && bounds.top < window.innerHeight
      && bounds.right > 0 && bounds.left < window.innerWidth;
    const canScroll = isVisible(element) && inViewport && maxScroll > 2;
    cues.left.hidden = !canScroll;
    cues.right.hidden = !canScroll;
    cues.left.disabled = !canScroll || element.scrollLeft <= 1;
    cues.right.disabled = !canScroll || element.scrollLeft >= maxScroll - 1;
  }

  function removeScrollCues(element, cues) {
    element.removeEventListener("scroll", cues.onScroll);
    element.removeEventListener("pointerdown", cues.onPointerDown);
    element.removeEventListener("pointermove", cues.onPointerMove);
    element.removeEventListener("pointerup", cues.onPointerUp);
    element.removeEventListener("pointercancel", cues.onPointerUp);
    element.removeEventListener("click", cues.onClickCapture, true);
    cues.shell.replaceWith(element);
    restoreInlineStyle(element, "grid-area", cues.originalGridArea);
    restoreInlineStyle(element, "margin", cues.originalMargin);
    restoreInlineStyle(element, "min-width", cues.originalMinWidth);
    restoreInlineStyle(element, "width", cues.originalWidth);
    element.classList.remove("horizontal-scroll-cue-target");
    element.classList.remove("horizontal-scroll-dragging");
    if (typeof ResizeObserver !== "undefined") resizeObserver.unobserve(element);
    horizontalScrollers.delete(element);
  }

  function addScrollCues(element) {
    const left = document.createElement("button");
    left.type = "button";
    left.className = "horizontal-scroll-cue horizontal-scroll-cue-left";
    left.setAttribute("aria-label", "Rolar para a esquerda");
    left.title = "Mostrar conteúdo anterior";
    left.innerHTML = '<span aria-hidden="true"></span>';

    const right = document.createElement("button");
    right.type = "button";
    right.className = "horizontal-scroll-cue horizontal-scroll-cue-right";
    right.setAttribute("aria-label", "Rolar para a direita");
    right.title = "Mostrar mais conteúdo";
    right.innerHTML = '<span aria-hidden="true"></span>';

    const cues = { left, right };
    cues.onScroll = () => updateScrollCues(element, cues);
    let dragStartX = 0;
    let dragStartScroll = 0;
    let dragging = false;
    let suppressClick = false;
    const onPointerDown = (event) => {
      if (event.pointerType !== "mouse" || event.button !== 0
        || event.target.closest("button, input, select, textarea, [contenteditable='true']")) return;
      dragStartX = event.clientX;
      dragStartScroll = element.scrollLeft;
      dragging = false;
      element.setPointerCapture(event.pointerId);
    };
    const onPointerMove = (event) => {
      if (!element.hasPointerCapture(event.pointerId)) return;
      const delta = event.clientX - dragStartX;
      if (!dragging && Math.abs(delta) > 5) {
        dragging = true;
        element.classList.add("horizontal-scroll-dragging");
      }
      if (dragging) {
        event.preventDefault();
        element.scrollLeft = dragStartScroll - delta;
      }
    };
    const onPointerUp = (event) => {
      if (!element.hasPointerCapture(event.pointerId)) return;
      element.releasePointerCapture(event.pointerId);
      if (dragging) suppressClick = true;
      dragging = false;
      element.classList.remove("horizontal-scroll-dragging");
    };
    const onClickCapture = (event) => {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    cues.onPointerDown = onPointerDown;
    cues.onPointerMove = onPointerMove;
    cues.onPointerUp = onPointerUp;
    cues.onClickCapture = onClickCapture;
    element.addEventListener("pointerdown", onPointerDown);
    element.addEventListener("pointermove", onPointerMove);
    element.addEventListener("pointerup", onPointerUp);
    element.addEventListener("pointercancel", onPointerUp);
    element.addEventListener("click", onClickCapture, true);
    const scrollByPage = (direction) => {
      element.scrollBy({
        left: direction * Math.max(120, element.clientWidth * 0.7),
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    };
    left.addEventListener("click", () => scrollByPage(-1));
    right.addEventListener("click", () => scrollByPage(1));

    const style = window.getComputedStyle(element);
    const shell = document.createElement("div");
    shell.className = "horizontal-scroll-cue-shell";
    if (style.gridArea !== "auto") shell.style.gridArea = style.gridArea;
    shell.style.margin = style.margin;
    shell.style.minWidth = "0";
    shell.style.maxWidth = style.maxWidth;
    shell.style.flex = style.flex;
    shell.style.order = style.order;
    shell.style.alignSelf = style.alignSelf;
    cues.shell = shell;
    cues.originalGridArea = saveInlineStyle(element, "grid-area");
    cues.originalMargin = saveInlineStyle(element, "margin");
    cues.originalMinWidth = saveInlineStyle(element, "min-width");
    cues.originalWidth = saveInlineStyle(element, "width");
    element.before(shell);
    shell.append(left, element, right);
    element.style.gridArea = "auto";
    element.style.margin = "0";
    element.style.minWidth = "0";
    element.style.width = "100%";
    element.classList.add("horizontal-scroll-cue-target");
    element.addEventListener("scroll", cues.onScroll, { passive: true });
    horizontalScrollers.set(element, cues);
    if (typeof ResizeObserver !== "undefined") resizeObserver.observe(element);
    updateScrollCues(element, cues);
  }

  function scanHorizontalScrollers() {
    scanScheduled = false;
    const candidates = new Set(document.querySelectorAll("*"));
    for (const [element, cues] of horizontalScrollers) {
      const overflowX = window.getComputedStyle(element).overflowX;
      if (!["auto", "scroll", "overlay"].includes(overflowX) || !isVisible(element)
        || element.scrollWidth - element.clientWidth <= 2) {
        removeScrollCues(element, cues);
      } else {
        updateScrollCues(element, cues);
      }
    }
    for (const element of candidates) {
      if (element === document.body || element === document.documentElement || horizontalScrollers.has(element)) continue;
      const overflowX = window.getComputedStyle(element).overflowX;
      if (["auto", "scroll", "overlay"].includes(overflowX) && isVisible(element)
        && element.scrollWidth - element.clientWidth > 2) {
        addScrollCues(element);
      }
    }
  }

  function scheduleScrollScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    window.requestAnimationFrame(scanHorizontalScrollers);
  }

  const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
    for (const { target } of entries) {
      const cues = horizontalScrollers.get(target);
      if (cues) updateScrollCues(target, cues);
    }
    scheduleScrollScan();
  });

  new MutationObserver((records) => {
    const shouldScan = records.some((record) => {
      if (record.type === "attributes" && record.target.closest(".horizontal-scroll-cue")) return false;
      if (record.type === "childList" && [...record.addedNodes].every((node) =>
        node.nodeType !== Node.ELEMENT_NODE || node.matches(".horizontal-scroll-cue"))) return false;
      return true;
    });
    if (shouldScan) scheduleScrollScan();
  }).observe(document.body, {
    attributes: true,
    attributeFilter: ["aria-hidden", "class", "hidden", "style"],
    childList: true,
    subtree: true,
  });
  window.addEventListener("resize", scheduleScrollScan, { passive: true });
  window.addEventListener("scroll", () => {
    for (const [element, cues] of horizontalScrollers) updateScrollCues(element, cues);
  }, { passive: true, capture: true });
  scheduleScrollScan();
  if (document.fonts?.ready) document.fonts.ready.then(scheduleScrollScan);

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    for (const button of themeButtons) {
      const nextTheme = theme === "dark" ? "light" : "dark";
      button.setAttribute("aria-label", `Ativar tema ${nextTheme === "dark" ? "escuro" : "claro"}`);
      button.setAttribute("title", `Ativar tema ${nextTheme === "dark" ? "escuro" : "claro"}`);
      button.setAttribute("aria-pressed", String(theme === "dark"));
    }
  }

  const initialTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  applyTheme(initialTheme);

  for (const button of themeButtons) {
    button.addEventListener("click", () => {
      const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
      try {
        localStorage.setItem("wiki-theme", nextTheme);
      } catch {
        // The theme still applies for this page when browser storage is unavailable.
      }
    });
  }

  if (mobileActions) {
    document.addEventListener("click", (event) => {
      if (mobileActions.open && !mobileActions.contains(event.target)) {
        mobileActions.open = false;
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") mobileActions.open = false;
    });
  }

  if (!articleForm) return;

  const source = articleForm.querySelector(".article-content-fallback");
  const initialData = document.querySelector(".editor-initial-data");
  const moduleList = articleForm.querySelector("[data-module-list]");
  const toolbar = articleForm.querySelector("[data-module-toolbar]");
  const moduleCount = articleForm.querySelector("[data-block-count]");
  const maxVideos = Number(initialData.dataset.maxVideos);
  const maxModules = Number(initialData.dataset.maxBlocks);
  const format = "wiki-blocks-v1";
  let blocks = JSON.parse(initialData.dataset.articleBlocks);

  if (blocks.length === 0) blocks = [{ type: "paragraph", text: "" }];

  function updateFormValue() {
    source.value = JSON.stringify({ format, blocks });
  }

  function makeButton(text, label, onClick, disabled = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mini-button module-action";
    button.textContent = text;
    button.setAttribute("aria-label", label);
    button.disabled = disabled;
    button.addEventListener("click", onClick);
    return button;
  }

  function makeField(labelText, value, options, onInput) {
    const label = document.createElement("label");
    label.className = "module-field";
    const caption = document.createElement("span");
    caption.textContent = labelText;
    const input = options.multiline
      ? document.createElement("textarea")
      : document.createElement("input");
    input.className = "form-control";
    input.value = value || "";
    input.required = options.required || false;
    if (options.type) input.type = options.type;
    if (options.maxlength) input.maxLength = options.maxlength;
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.rows) input.rows = options.rows;
    input.addEventListener("input", () => onInput(input.value));
    label.append(caption, input);
    return label;
  }

  function renderModules() {
    moduleList.replaceChildren();
    const videoCount = blocks.filter((block) => block.type === "youtube").length;

    blocks.forEach((block, index) => {
      const card = document.createElement("section");
      card.className = "content-module";
      const heading = document.createElement("div");
      heading.className = "module-card-heading";
      const label = document.createElement("strong");
      label.textContent = {
        paragraph: "Texto",
        heading: "Título",
        image: "Imagem",
        youtube: "Vídeo do YouTube",
      }[block.type] || "Módulo";
      const actions = document.createElement("div");
      actions.className = "module-card-actions";
      actions.append(
        makeButton("↑", "Mover módulo para cima", () => moveBlock(index, -1), index === 0),
        makeButton("↓", "Mover módulo para baixo", () => moveBlock(index, 1), index === blocks.length - 1),
        makeButton("Remover", "Remover módulo", () => removeBlock(index)),
      );
      heading.append(label, actions);
      card.append(heading);

      if (block.type === "paragraph") {
        card.append(makeField("Texto do módulo", block.text, {
          multiline: true,
          rows: 5,
          maxlength: 20000,
          placeholder: "Escreva um parágrafo...",
        }, (value) => { block.text = value; updateFormValue(); }));
      } else if (block.type === "heading") {
        card.append(makeField("Título", block.text, {
          maxlength: 20000,
          placeholder: "Título da seção",
        }, (value) => { block.text = value; updateFormValue(); }));
      } else if (block.type === "image") {
        card.append(
          makeField("URL HTTPS da imagem", block.url, {
            type: "url",
            required: true,
            maxlength: 2048,
            placeholder: "https://exemplo.com/imagem.jpg",
          }, (value) => { block.url = value; updateFormValue(); }),
          makeField("Descrição da imagem (texto alternativo)", block.alt, {
            required: true,
            maxlength: 300,
            placeholder: "Descreva a imagem para leitores de tela",
          }, (value) => { block.alt = value; updateFormValue(); }),
        );
      } else if (block.type === "youtube") {
        card.append(
          makeField("Link do vídeo no YouTube", block.url, {
            type: "url",
            required: true,
            maxlength: 2048,
            placeholder: "https://www.youtube.com/watch?v=...",
          }, (value) => { block.url = value; updateFormValue(); }),
        );
        const help = document.createElement("small");
        help.className = "module-help";
        help.textContent = `Vídeos incorporados: ${videoCount} de ${maxVideos}.`;
        card.append(help);
      }
      moduleList.append(card);
    });

    moduleCount.textContent = `${blocks.length} de ${maxModules} módulos · ${videoCount} de ${maxVideos} vídeos`;
    for (const button of toolbar.querySelectorAll("[data-add-module]")) {
      button.disabled = blocks.length >= maxModules
        || (button.dataset.addModule === "youtube" && videoCount >= maxVideos);
    }
    updateFormValue();
  }

  function moveBlock(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= blocks.length) return;
    [blocks[index], blocks[target]] = [blocks[target], blocks[index]];
    renderModules();
  }

  function removeBlock(index) {
    blocks.splice(index, 1);
    renderModules();
  }

  toolbar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-add-module]");
    if (!button || button.disabled) return;
    const type = button.dataset.addModule;
    blocks.push(type === "image"
      ? { type, url: "", alt: "" }
      : type === "youtube"
        ? { type, url: "" }
        : { type, text: "" });
    renderModules();
  });

  source.hidden = true;
  source.required = false;
  toolbar.hidden = false;
  moduleList.hidden = false;
  articleForm.addEventListener("submit", updateFormValue);
  renderModules();
})();
