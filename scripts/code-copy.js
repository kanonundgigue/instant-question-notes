(() => {
  const COPY_LABEL = "コピー";
  const COPIED_LABEL = "コピー済み";
  const FAILED_LABEL = "失敗";
  const LANGUAGE_LABELS = {
    fortran: "Fortran 90",
    python: "Python",
    shell: "Shell",
  };
  const KEYWORDS = {
    fortran: [
      "allocate", "allocatable", "call", "case", "character", "class",
      "contains", "cycle", "deallocate", "dimension", "do", "else",
      "elseif", "elsewhere", "end", "enddo", "endif", "entry", "exit",
      "function", "if", "implicit", "in", "integer", "intent", "interface",
      "logical", "module", "none", "only", "operator", "optional", "parameter",
      "pointer", "private", "program", "public", "pure", "real", "recursive",
      "result", "return", "select", "stop", "subroutine", "target", "then",
      "type", "use", "where", "while",
    ],
    python: [
      "and", "as", "assert", "async", "await", "break", "case", "class",
      "continue", "def", "del", "elif", "else", "except", "False", "finally",
      "for", "from", "global", "if", "import", "in", "is", "lambda", "match",
      "None", "nonlocal", "not", "or", "pass", "raise", "return", "True",
      "try", "while", "with", "yield",
    ],
    shell: [
      "case", "cd", "chmod", "cp", "curl", "do", "done", "echo", "elif",
      "else", "esac", "export", "fi", "for", "function", "git", "if", "in",
      "local", "mkdir", "mv", "osascript", "python", "python3", "readonly",
      "select", "then", "until", "uv", "while",
    ],
  };

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

  const escapeHtml = (text) => text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");

  const detectLanguage = (code) => {
    const className = [...code.classList]
      .find((name) => name.startsWith("language-"));
    const declared = className?.replace("language-", "").toLowerCase();

    if (declared === "bash" || declared === "sh" || declared === "shell") {
      return "shell";
    }
    if (declared === "fortran" || declared === "fortran90" || declared === "f90") {
      return "fortran";
    }
    if (declared === "py" || declared === "python") {
      return "python";
    }

    const source = code.textContent ?? "";
    if (/^\s*(?:from\s+\w+(?:\.\w+)*\s+import|import\s+\w+|def\s+\w+\s*\(|class\s+\w+[:(])/m.test(source)) {
      return "python";
    }
    if (/^\s*(?:program|module|subroutine|function)\s+\w+|^\s*implicit\s+none\b/im.test(source)) {
      return "fortran";
    }
    if (/^#!.*\b(?:ba|z|k)?sh\b|^\s*(?:curl|git|python3?|uv|cp|mv|mkdir|chmod|osascript)\b/m.test(source)) {
      return "shell";
    }
    return null;
  };

  const highlightCode = (source, language) => {
    const keywords = KEYWORDS[language];
    const keywordPattern = keywords
      .map((keyword) => keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|");
    const flags = language === "fortran" ? "gim" : "gm";
    const comment = language === "fortran" ? "![^\\n]*" : "#[^\\n]*";
    const tripleString = language === "python"
      ? "|(?:'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\")"
      : "";
    const pattern = new RegExp(
      `(${tripleString ? tripleString.slice(1) + "|" : ""}'(?:\\\\.|[^'\\\\])*'|"(?:\\\\.|[^"\\\\])*")`
      + `|(${comment})`
      + `|\\b(${keywordPattern})\\b`
      + "|\\b(\\d+(?:\\.\\d+)?(?:[eEdD][+-]?\\d+)?)\\b"
      + "|\\b([A-Za-z_]\\w*)(?=\\s*\\()",
      flags,
    );

    let result = "";
    let lastIndex = 0;
    for (const match of source.matchAll(pattern)) {
      result += escapeHtml(source.slice(lastIndex, match.index));
      const tokenClass = match[1]
        ? "string"
        : match[2]
          ? "comment"
          : match[3]
            ? "keyword"
            : match[4]
              ? "number"
              : "function";
      result += `<span class="syntax-${tokenClass}">${escapeHtml(match[0])}</span>`;
      lastIndex = (match.index ?? 0) + match[0].length;
    }
    return result + escapeHtml(source.slice(lastIndex));
  };

  document.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    if (!pre || pre.parentElement?.classList.contains("code-block")) {
      return;
    }
    const source = code.textContent ?? "";
    const language = detectLanguage(code);

    if (language) {
      code.classList.add(`language-${language}`);
      code.innerHTML = highlightCode(source, language);
    }

    const wrapper = document.createElement("div");
    wrapper.className = "code-block";

    const toolbar = document.createElement("div");
    toolbar.className = "code-block__toolbar";

    if (language) {
      const languageLabel = document.createElement("span");
      languageLabel.className = "code-block__language";
      languageLabel.textContent = LANGUAGE_LABELS[language];
      toolbar.appendChild(languageLabel);
    }

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
        await copyText(source);
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
