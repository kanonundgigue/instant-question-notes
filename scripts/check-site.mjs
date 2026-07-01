import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const rootDir = process.cwd();
const publicRoots = ["index.html", "notes"];
const ignoredDirs = new Set([
  ".git",
  ".github",
  ".playwright-mcp",
  "_drafts",
  "_site",
  "node_modules",
  "outputs",
  "tools",
]);
const ignoredPathParts = new Set(["_template"]);
const errors = [];

function toPosix(filePath) {
  return filePath.split(path.sep).join("/");
}

function addError(filePath, message) {
  errors.push(`${toPosix(path.relative(rootDir, filePath))}: ${message}`);
}

function walk(targetPath) {
  if (!existsSync(targetPath)) {
    return [];
  }

  const stats = statSync(targetPath);
  if (stats.isFile()) {
    return [targetPath];
  }

  if (!stats.isDirectory()) {
    return [];
  }

  const baseName = path.basename(targetPath);
  if (ignoredDirs.has(baseName) || ignoredPathParts.has(baseName)) {
    return [];
  }

  const files = [];
  for (const entry of readdirSync(targetPath)) {
    files.push(...walk(path.join(targetPath, entry)));
  }
  return files;
}

function collectPublicHtmlFiles() {
  return publicRoots
    .flatMap((entry) => walk(path.join(rootDir, entry)))
    .filter((filePath) => filePath.endsWith(".html"))
    .filter((filePath) => !filePath.endsWith(".draft.html"));
}

function stripFragmentsAndQuery(href) {
  return href.split("#")[0].split("?")[0];
}

function isExternalHref(href) {
  return /^(https?:|mailto:|tel:|javascript:|data:)/i.test(href);
}

function decodeHref(href) {
  try {
    return decodeURIComponent(href);
  } catch {
    return href;
  }
}

function checkRelativeLinks(filePath, html) {
  const hrefPattern = /\bhref=(["'])(.*?)\1/gims;
  for (const match of html.matchAll(hrefPattern)) {
    const rawHref = match[2].trim();
    if (!rawHref || rawHref.startsWith("#") || isExternalHref(rawHref)) {
      continue;
    }

    const cleanHref = stripFragmentsAndQuery(decodeHref(rawHref));
    if (!cleanHref) {
      continue;
    }

    const linkedPath = path.resolve(path.dirname(filePath), cleanHref);
    if (!linkedPath.startsWith(rootDir)) {
      addError(filePath, `リポジトリ外への相対リンクがあります: ${rawHref}`);
      continue;
    }

    if (!existsSync(linkedPath)) {
      addError(filePath, `リンク先が見つかりません: ${rawHref}`);
    }
  }
}

function checkMobileTables(filePath, html) {
  const tablePattern = /<table\b[^>]*class=(["'])[^"']*\bmobile-table\b[^"']*\1[^>]*>[\s\S]*?<\/table>/gim;
  for (const tableMatch of html.matchAll(tablePattern)) {
    const tableHtml = tableMatch[0];
    const tdPattern = /<td\b(?![^>]*\bdata-label=)[^>]*>/gim;
    if (tdPattern.test(tableHtml)) {
      addError(filePath, "mobile-table 内に data-label のない td があります");
    }
  }
}

function checkProtectedArticle(filePath, html) {
  const relativePath = toPosix(path.relative(rootDir, filePath));
  if (!relativePath.startsWith("notes/private/") || relativePath.endsWith("/index.html")) {
    return;
  }

  if (!/\bid=(["'])note-payload\1/.test(html)) {
    addError(filePath, "保護記事に note-payload がありません");
  }

  if (!/\bsrc=(["'])\.\.\/\.\.\/scripts\/decrypt\.js\1/.test(html)) {
    addError(filePath, "保護記事が scripts/decrypt.js を読み込んでいません");
  }
}

function checkHtmlFile(filePath) {
  const html = readFileSync(filePath, "utf8");

  if (html.includes("{{") || html.includes("}}")) {
    addError(filePath, "テンプレートプレースホルダが残っています");
  }

  checkRelativeLinks(filePath, html);
  checkMobileTables(filePath, html);
  checkProtectedArticle(filePath, html);
}

function checkTextFileContains(filePath, requiredValues, label) {
  const content = readFileSync(filePath, "utf8");
  for (const value of requiredValues) {
    if (!content.includes(value)) {
      addError(filePath, `${label} に ${value} がありません`);
    }
  }
}

function checkConfigurationGuards() {
  checkTextFileContains(
    path.join(rootDir, ".github", "workflows", "pages.yml"),
    ["--exclude='_drafts/'", "--exclude='*.draft.html'", "--exclude='PROTECTED_NOTES.local.md'"],
    "GitHub Pages の除外設定",
  );

  checkTextFileContains(
    path.join(rootDir, ".gitignore"),
    ["_site/", ".playwright-mcp/", "outputs/", "_drafts/", "notes/**/*.draft.html", "PROTECTED_NOTES.local.md"],
    ".gitignore",
  );
}

const htmlFiles = collectPublicHtmlFiles();
for (const filePath of htmlFiles) {
  checkHtmlFile(filePath);
}
checkConfigurationGuards();

if (errors.length > 0) {
  console.error("site check failed");
  for (const error of errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(`site check ok (${htmlFiles.length} HTML files)`);
