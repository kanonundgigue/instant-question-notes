(() => {
  const COPY_LABEL = "コピー";
  const COPIED_LABEL = "コピー済み";
  const FAILED_LABEL = "失敗";

  const copyText = async (text) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.inset = "0 auto auto -9999px";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();

    if (!copied) {
      throw new Error("copy failed");
    }
  };

  const setButtonLabel = (button, label) => {
    button.textContent = label;
    button.setAttribute("aria-label", `コードブロックを${label}`);
  };

  document.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    if (!pre || pre.parentElement?.classList.contains("code-block")) {
      return;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "code-block";

    const toolbar = document.createElement("div");
    toolbar.className = "code-block__toolbar";

    const button = document.createElement("button");
    button.className = "code-block__copy";
    button.type = "button";
    setButtonLabel(button, COPY_LABEL);

    pre.parentNode?.insertBefore(wrapper, pre);
    wrapper.appendChild(toolbar);
    toolbar.appendChild(button);
    wrapper.appendChild(pre);

    button.addEventListener("click", async () => {
      try {
        await copyText(code.textContent ?? "");
        setButtonLabel(button, COPIED_LABEL);
      } catch {
        setButtonLabel(button, FAILED_LABEL);
      }

      window.setTimeout(() => {
        setButtonLabel(button, COPY_LABEL);
      }, 1600);
    });
  });
})();
