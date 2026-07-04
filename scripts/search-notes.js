const searchInput = document.querySelector("#note-search");
const searchResults = document.querySelector("#note-search-results");
const searchStatus = document.querySelector("#note-search-status");
const clearButton = document.querySelector(".search-form__clear");
const searchForm = document.querySelector(".search-form");

function normalizeText(value) {
  return value.normalize("NFKC").toLocaleLowerCase("ja-JP").trim();
}

function getCardText(card, selector) {
  return card.querySelector(selector)?.textContent.trim() ?? "";
}

function createEmptyState() {
  const element = document.createElement("div");
  element.className = "empty-state";
  element.innerHTML = "<p>該当する記事はありません。</p>";
  return element;
}

function collectNotes() {
  const sections = [
    ...document.querySelectorAll(".topic-section:not(#recent)"),
    document.querySelector("#recent"),
  ].filter(Boolean);
  const notes = new Map();

  for (const section of sections) {
    const sectionName = getCardText(section, "h2");
    const cards = section.querySelectorAll(".note-card");

    for (const card of cards) {
      const link = card.querySelector("h3 a");
      if (!link) {
        continue;
      }

      const href = link.getAttribute("href");
      if (!href || notes.has(href)) {
        continue;
      }

      const category = getCardText(card, ".note-card__category");
      const title = link.textContent.trim();
      const summary = getCardText(card, ".note-card__summary");
      const date = getCardText(card, ".note-card__date");
      const text = normalizeText([sectionName, category, title, summary, date, href].join(" "));

      notes.set(href, {
        card,
        text,
      });
    }
  }

  return [...notes.values()];
}

const notes = collectNotes();

function renderResults(query) {
  const terms = normalizeText(query).split(/\s+/).filter(Boolean);

  searchResults.replaceChildren();
  clearButton.hidden = terms.length === 0;

  if (terms.length === 0) {
    searchResults.hidden = true;
    searchStatus.textContent = "検索待ち";
    return;
  }

  const matches = notes.filter((note) => terms.every((term) => note.text.includes(term)));

  if (matches.length === 0) {
    searchResults.append(createEmptyState());
    searchResults.hidden = false;
    searchStatus.textContent = "0 件";
    return;
  }

  for (const match of matches) {
    searchResults.append(match.card.cloneNode(true));
  }

  searchResults.hidden = false;
  searchStatus.textContent = `${matches.length} 件`;
}

searchForm?.addEventListener("submit", (event) => {
  event.preventDefault();
});

searchInput?.addEventListener("input", (event) => {
  renderResults(event.currentTarget.value);
});

clearButton?.addEventListener("click", () => {
  searchInput.value = "";
  renderResults("");
  searchInput.focus();
});
