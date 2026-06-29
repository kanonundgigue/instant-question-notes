const C = {
  paper: "#F7F4ED",
  white: "#FFFFFF",
  ink: "#172026",
  muted: "#526069",
  hair: "#D8D2C5",
  accent: "#006C67",
  accentSoft: "#DCEDEA",
  support: "#B65A3A",
  supportSoft: "#F1E0D8",
  pale: "#ECE6D8",
};

const FONT = {
  title: "Hiragino Sans",
  body: "Hiragino Sans",
  mono: "Aptos Mono",
};

const SOURCE = "鈴木亨「学会発表の基本テクニック」日本義肢装具学会誌 26(1), 2010";

function addBg(slide, ctx) {
  ctx.addShape(slide, {
    x: 0,
    y: 0,
    width: 1280,
    height: 720,
    fill: C.paper,
    line: ctx.line("#00000000", 0),
  });
}

function text(slide, ctx, value, x, y, width, height, options = {}) {
  return ctx.addText(slide, {
    text: value,
    x,
    y,
    width,
    height,
    fontSize: options.size ?? 24,
    color: options.color ?? C.ink,
    bold: options.bold ?? false,
    typeface: options.face ?? FONT.body,
    align: options.align ?? "left",
    valign: options.valign ?? "top",
    fill: options.fill ?? "#00000000",
    line: options.line ?? ctx.line("#00000000", 0),
    insets: options.insets ?? { left: 0, right: 0, top: 0, bottom: 0 },
    name: options.name,
  });
}

function rect(slide, ctx, x, y, width, height, fill = C.white, lineFill = "#00000000", lineWidth = 0, name) {
  return ctx.addShape(slide, {
    x,
    y,
    width,
    height,
    fill,
    line: ctx.line(lineFill, lineWidth),
    name,
  });
}

function rule(slide, ctx, x, y, width, height = 2, fill = C.hair) {
  rect(slide, ctx, x, y, width, height, fill);
}

function footer(slide, ctx, n, extra = "") {
  rule(slide, ctx, 64, 664, 1010, 1, C.hair);
  text(slide, ctx, extra || SOURCE, 64, 675, 900, 18, {
    size: 10,
    color: C.muted,
    face: FONT.body,
  });
  text(slide, ctx, String(n).padStart(2, "0"), 1128, 669, 88, 28, {
    size: 18,
    color: C.accent,
    bold: true,
    align: "right",
    face: FONT.body,
  });
}

function kicker(slide, ctx, value, x = 64, y = 54) {
  rect(slide, ctx, x, y + 10, 26, 4, C.accent, C.accent, 0, `kicker-${value}-marker`);
  text(slide, ctx, value, x + 40, y, 260, 24, {
    size: 15,
    color: C.accent,
    bold: true,
    face: FONT.body,
    name: `kicker-${value}-label`,
  });
}

function claim(slide, ctx, value, x = 64, y = 86, width = 920, size = 40) {
  text(slide, ctx, value, x, y, width, 104, {
    size,
    color: C.ink,
    bold: true,
    face: FONT.title,
  });
}

function bullet(slide, ctx, lines, x, y, width, rowH = 50, options = {}) {
  lines.forEach((line, index) => {
    const top = y + rowH * index;
    rect(slide, ctx, x, top + 10, 8, 8, options.dot ?? C.accent);
    text(slide, ctx, line, x + 24, top, width - 24, rowH, {
      size: options.size ?? 23,
      color: options.color ?? C.ink,
      face: FONT.body,
    });
  });
}

function labeledBox(slide, ctx, label, body, x, y, width, height, options = {}) {
  rect(slide, ctx, x, y, width, height, options.fill ?? C.white, options.lineFill ?? C.hair, 1);
  text(slide, ctx, label, x + 22, y + 20, width - 44, 26, {
    size: options.labelSize ?? 16,
    color: options.labelColor ?? C.accent,
    bold: true,
    face: FONT.body,
  });
  text(slide, ctx, body, x + 22, y + 58, width - 44, height - 70, {
    size: options.bodySize ?? 24,
    color: options.bodyColor ?? C.ink,
    bold: options.bodyBold ?? false,
    face: FONT.body,
  });
}

function sectionLabel(slide, ctx, value, x, y, fill = C.accent) {
  rect(slide, ctx, x, y, 42, 42, fill);
  text(slide, ctx, value, x, y + 2, 42, 42, {
    size: 22,
    color: C.white,
    bold: true,
    align: "center",
    valign: "middle",
    face: FONT.body,
  });
}

async function addFigure(slide, ctx, assetName, x, y, width, height) {
  await ctx.addImage(slide, {
    path: `${ctx.assetDir}/${assetName}`,
    x,
    y,
    width,
    height,
    fit: "contain",
    alt: assetName,
  });
}

async function slide01(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  text(slide, ctx, "研究発表", 64, 58, 220, 34, {
    size: 18,
    color: C.accent,
    bold: true,
  });
  rule(slide, ctx, 64, 112, 1030, 2, C.hair);
  text(slide, ctx, "学会発表は\n「読む」より\n「伝える」が本体", 64, 160, 670, 260, {
    size: 57,
    color: C.ink,
    bold: true,
    face: FONT.title,
  });
  rect(slide, ctx, 792, 150, 360, 360, C.white, C.hair, 1);
  text(slide, ctx, "出典論文", 828, 188, 280, 28, {
    size: 18,
    color: C.accent,
    bold: true,
  });
  text(slide, ctx, "鈴木 亨\n学会発表の\n基本テクニック", 828, 240, 280, 88, {
    size: 26,
    color: C.ink,
    bold: true,
  });
  text(slide, ctx, "日本義肢装具学会誌\nVol.26 No.1, 2010\npp.46-50", 828, 358, 280, 96, {
    size: 21,
    color: C.muted,
  });
  rect(slide, ctx, 64, 494, 640, 72, C.accentSoft);
  text(slide, ctx, "10分前後の口演を想定した、発表準備の実践チェック資料", 88, 516, 590, 32, {
    size: 22,
    color: C.accent,
    bold: true,
  });
  footer(slide, ctx, 1, "J-STAGE PDF: https://www.jstage.jst.go.jp/article/jspo/26/1/26_46/_pdf/-char/ja");
  return slide;
}

async function slide02(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "THESIS");
  claim(slide, ctx, "発表技術の中心は、聴衆に要点を残す設計である。", 64, 92, 980, 40);
  const labels = [
    ["態度", "聴衆を見る\n声・視線・姿勢で\n伝える"],
    ["設計", "誰に何を伝えるか\n最初に決める"],
    ["資料", "1枚1情報に近づけ\n説明時間から\n逆算する"],
    ["質疑", "答え方を準備し\n誠実に返す"],
  ];
  labels.forEach(([label, body], i) => {
    const x = 92 + i * 286;
    rect(slide, ctx, x, 292, 222, 190, C.white, C.hair, 1);
    text(slide, ctx, `0${i + 1}`, x + 20, 316, 48, 28, {
      size: 18,
      color: C.accent,
      bold: true,
    });
    text(slide, ctx, label, x + 20, 352, 180, 44, {
      size: 33,
      color: C.ink,
      bold: true,
      face: FONT.title,
    });
    text(slide, ctx, body, x + 20, 410, 176, 56, {
      size: 18,
      color: C.muted,
    });
    if (i < labels.length - 1) {
      rule(slide, ctx, x + 232, 386, 34, 2, C.accent);
    }
  });
  text(slide, ctx, "論文全体は、発表を「話し方」「準備」「当日の対応」まで含む\n総合的なコミュニケーションとして扱っている。", 122, 548, 980, 54, {
    size: 22,
    color: C.muted,
  });
  footer(slide, ctx, 2);
  return slide;
}

async function slide03(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "RECEPTION");
  claim(slide, ctx, "聴衆が受け取る印象は、言葉だけでは決まらない。", 64, 92, 980, 40);
  const bars = [
    ["言葉", 7, C.muted],
    ["声", 38, C.accent],
    ["態度", 55, C.ink],
  ];
  let x = 116;
  bars.forEach(([label, value, color]) => {
    const w = value * 8.7;
    rect(slide, ctx, x, 270, w, 76, color);
    text(slide, ctx, `${value}%`, x + 14, 284, Math.max(w - 28, 48), 32, {
      size: 25,
      color: C.white,
      bold: true,
      align: "center",
    });
    text(slide, ctx, label, x, 364, w, 28, {
      size: 21,
      color,
      bold: true,
      align: "center",
    });
    x += w;
  });
  rule(slide, ctx, 116, 404, 870, 2, C.hair);
  labeledBox(slide, ctx, "このスライドの読み方", "原稿の言葉は重要。ただし、\n声の大きさ・抑揚・視線・姿勢が、\n聴衆の理解と印象を大きく左右する。", 728, 454, 390, 144, {
    fill: C.white,
    bodySize: 19,
  });
  bullet(slide, ctx, ["スライドを見続けない", "前を向いて話す", "重要点で聴衆と目を合わせる"], 128, 462, 530, 42, {
    size: 22,
  });
  footer(slide, ctx, 3, "論文内で紹介されたコミュニケーション要素の比率を要約");
  return slide;
}

async function slide04(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "POSTURE");
  claim(slide, ctx, "発表態度は、聴衆を見ることから始まる。", 64, 92, 920, 40);
  rect(slide, ctx, 90, 228, 500, 310, C.white, C.hair, 1);
  rect(slide, ctx, 690, 228, 500, 310, C.supportSoft, C.hair, 1);
  text(slide, ctx, "する", 120, 258, 160, 38, {
    size: 31,
    color: C.accent,
    bold: true,
  });
  text(slide, ctx, "避ける", 720, 258, 180, 38, {
    size: 31,
    color: C.support,
    bold: true,
  });
  bullet(slide, ctx, ["会場を見渡す", "数人とアイコンタクトを取る", "要点では顔を上げる", "推敲した言葉でゆっくり話す"], 124, 330, 410, 42, {
    size: 22,
    dot: C.accent,
  });
  bullet(slide, ctx, ["原稿だけを見る", "ポインターをもてあそぶ", "髪や顔を触る", "早口で時間を埋める"], 724, 330, 410, 42, {
    size: 22,
    dot: C.support,
  });
  text(slide, ctx, "発表者が聴衆に向き合うほど、聴衆も発表に参加しやすくなる。", 132, 574, 920, 34, {
    size: 24,
    color: C.ink,
    bold: true,
  });
  footer(slide, ctx, 4);
  return slide;
}

async function slide05(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "AUDIENCE");
  claim(slide, ctx, "内容は、聴衆の専門性と参加目的に合わせて決める。", 64, 92, 980, 40);
  const nodes = [
    ["学術背景", "専門用語を\nどこまで説明するか"],
    ["参加目的", "何を聞きたいと\n思っているか"],
    ["関心", "発表者と同じ\n問題意識があるか"],
  ];
  nodes.forEach(([label, body], i) => {
    const x = 88 + i * 296;
    sectionLabel(slide, ctx, String(i + 1), x, 260, C.accent);
    text(slide, ctx, label, x + 58, 252, 190, 36, {
      size: 28,
      color: C.ink,
      bold: true,
    });
    text(slide, ctx, body, x + 58, 302, 210, 66, {
      size: 20,
      color: C.muted,
    });
    if (i < 2) {
      rule(slide, ctx, x + 252, 280, 52, 2, C.hair);
    }
  });
  rect(slide, ctx, 952, 238, 208, 166, C.accent, C.accent, 0);
  text(slide, ctx, "発表を\nデザインする", 966, 281, 180, 72, {
    size: 27,
    color: C.white,
    bold: true,
    align: "center",
    valign: "middle",
  });
  text(slide, ctx, "聴衆を想定しない発表は、説明量も用語選択も偶然に任せることになる。", 120, 514, 940, 60, {
    size: 25,
    color: C.ink,
    bold: true,
  });
  footer(slide, ctx, 5);
  return slide;
}

async function slide06(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "FOCUS");
  claim(slide, ctx, "短い口演では、研究全体ではなく3点を残す。", 64, 92, 960, 40);
  rect(slide, ctx, 132, 236, 920, 66, C.white, C.hair, 1);
  text(slide, ctx, "半年から1年かけた研究全体", 160, 250, 860, 36, {
    size: 26,
    color: C.muted,
    bold: true,
    align: "center",
  });
  rect(slide, ctx, 230, 338, 720, 58, C.pale, C.hair, 1);
  text(slide, ctx, "10分前後の発表時間", 260, 350, 660, 32, {
    size: 24,
    color: C.ink,
    bold: true,
    align: "center",
  });
  const kept = ["背景の必然性", "方法・結果の核", "数日後に残る結論"];
  kept.forEach((value, i) => {
    const x = 172 + i * 304;
    rect(slide, ctx, x, 468, 244, 92, C.white, C.hair, 1);
    text(slide, ctx, value, x + 18, 493, 208, 42, {
      size: 22,
      color: C.ink,
      bold: true,
      align: "center",
      valign: "middle",
    });
  });
  rule(slide, ctx, 590, 306, 2, 32, C.hair);
  rule(slide, ctx, 590, 402, 2, 52, C.hair);
  footer(slide, ctx, 6);
  return slide;
}

async function slide07(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "SCRIPT");
  claim(slide, ctx, "原稿は読む台本ではなく、論理を整える設計図である。", 64, 92, 1000, 40);
  labeledBox(slide, ctx, "速度", "初心者は\n250-270字/分\n程度を目安にする", 92, 250, 284, 204, {
    fill: C.white,
    bodySize: 28,
    bodyBold: true,
  });
  labeledBox(slide, ctx, "文", "1つの文には\n1つの考えだけを\n入れる", 406, 250, 284, 204, {
    fill: C.white,
    bodySize: 27,
    bodyBold: true,
  });
  labeledBox(slide, ctx, "言葉", "難語・長い修飾語\n二重否定を避ける", 720, 250, 284, 204, {
    fill: C.white,
    bodySize: 27,
    bodyBold: true,
  });
  rect(slide, ctx, 94, 500, 908, 62, C.accentSoft);
  text(slide, ctx, "原稿は、スライドの内容を口頭で説明できるように準備する。", 124, 517, 848, 30, {
    size: 23,
    color: C.accent,
    bold: true,
  });
  footer(slide, ctx, 7);
  return slide;
}

async function slide08(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "PACING");
  claim(slide, ctx, "スライドは1分1枚を出発点に、説明時間から逆算する。", 64, 92, 1030, 40);
  text(slide, ctx, "10分枠の例", 110, 236, 220, 30, {
    size: 24,
    color: C.ink,
    bold: true,
  });
  rect(slide, ctx, 110, 300, 700, 52, C.accent);
  rect(slide, ctx, 810, 300, 300, 52, C.support);
  text(slide, ctx, "発表 7分", 126, 312, 660, 28, {
    size: 24,
    color: C.white,
    bold: true,
    align: "center",
  });
  text(slide, ctx, "質疑 3分", 826, 312, 268, 28, {
    size: 24,
    color: C.white,
    bold: true,
    align: "center",
  });
  for (let i = 0; i <= 10; i += 1) {
    const x = 110 + i * 100;
    rule(slide, ctx, x, 284, 1, 92, C.hair);
    text(slide, ctx, `${i}`, x - 9, 382, 18, 20, {
      size: 13,
      color: C.muted,
      align: "center",
    });
  }
  labeledBox(slide, ctx, "注意", "13-14枚を7分で話すと、1枚あたり約30秒。\n図表の説明は追いつきにくい。", 162, 470, 816, 124, {
    fill: C.white,
    bodySize: 24,
    bodyBold: true,
    labelColor: C.support,
  });
  footer(slide, ctx, 8);
  return slide;
}

async function slide09(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "ANTI-PATTERN");
  claim(slide, ctx, "情報を詰め込むと、聴衆は読むだけで時間を失う。", 64, 92, 980, 40);
  rect(slide, ctx, 76, 210, 730, 380, C.white, C.hair, 1);
  await addFigure(slide, ctx, "jspo_fig1_too_much.png", 96, 228, 690, 338);
  labeledBox(slide, ctx, "どこが厳しいか", "・グラフが2つある\n・文字とラベルが小さい\n・説明対象が多くなる\n・1分では読み切れない", 846, 226, 302, 266, {
    fill: C.supportSoft,
    labelColor: C.support,
    bodySize: 22,
  });
  text(slide, ctx, "論文中の悪いスライド例を引用。\n図の中身ではなく、\n情報量の問題を示している。", 848, 502, 300, 62, {
    size: 18,
    color: C.muted,
  });
  footer(slide, ctx, 9, "引用図: 鈴木亨 2010, 図1");
  return slide;
}

async function slide10(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "REVISION");
  claim(slide, ctx, "図表を分けると、説明する対象が明確になる。", 64, 92, 930, 40);
  rect(slide, ctx, 96, 218, 690, 356, C.white, C.hair, 1);
  await addFigure(slide, ctx, "jspo_fig2_focused.png", 126, 250, 628, 274);
  labeledBox(slide, ctx, "改善の方向", "1枚に1つの図表\n文字を大きくする\n論点を先に決める", 846, 236, 290, 204, {
    fill: C.accentSoft,
    labelColor: C.accent,
    bodySize: 24,
    bodyBold: true,
  });
  rect(slide, ctx, 846, 470, 290, 82, C.white, C.hair, 1);
  text(slide, ctx, "スライドと論文図表は別物。発表用には削る判断が必要。", 866, 484, 250, 48, {
    size: 19,
    color: C.ink,
    bold: true,
  });
  footer(slide, ctx, 10, "引用図: 鈴木亨 2010, 図2");
  return slide;
}

async function slide11(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "Q&A");
  claim(slide, ctx, "質疑応答は、答え方を3パターンで準備する。", 64, 92, 940, 40);
  const rows = [
    ["意味も答えもわかる", "端的に答える", C.accentSoft, C.accent],
    ["意味はわかるが未検討", "今後の検討課題として受け止める", C.white, C.ink],
    ["意味がわからない", "論点を確認して聞き返す", C.supportSoft, C.support],
  ];
  rows.forEach(([state, response, fill, color], i) => {
    const y = 230 + i * 116;
    rect(slide, ctx, 108, y, 404, 82, fill, C.hair, 1);
    rect(slide, ctx, 512, y, 560, 82, C.white, C.hair, 1);
    text(slide, ctx, state, 134, y + 22, 346, 32, {
      size: 24,
      color,
      bold: true,
    });
    text(slide, ctx, response, 540, y + 22, 500, 34, {
      size: 24,
      color: C.ink,
      bold: true,
    });
  });
  text(slide, ctx, "はぐらかすより、質問の意味を明確にしてから単文で返す。", 128, 598, 840, 30, {
    size: 24,
    color: C.ink,
    bold: true,
  });
  footer(slide, ctx, 11);
  return slide;
}

async function slide12(presentation, ctx) {
  const slide = presentation.slides.add();
  addBg(slide, ctx);
  kicker(slide, ctx, "CHECKLIST");
  claim(slide, ctx, "発表前の確認は、6項目まで絞って見る。", 64, 92, 900, 40);
  const items = [
    "聴衆を想定したか",
    "要点は3つ以内か",
    "時間内に終わるか",
    "文字は読めるか",
    "原稿を読まずに話せるか",
    "質疑への返し方を準備したか",
  ];
  items.forEach((item, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 120 + col * 500;
    const y = 238 + row * 104;
    rect(slide, ctx, x, y, 430, 68, C.white, C.hair, 1);
    text(slide, ctx, "✓", x + 24, y + 15, 34, 34, {
      size: 26,
      color: C.accent,
      bold: true,
      align: "center",
    });
    text(slide, ctx, item, x + 72, y + 18, 326, 32, {
      size: 23,
      color: C.ink,
      bold: true,
    });
  });
  rect(slide, ctx, 118, 586, 932, 44, C.accentSoft);
  text(slide, ctx, "発表の目的は、すべてを話すことではなく、聴衆に次の理解を残すこと。", 144, 597, 880, 24, {
    size: 22,
    color: C.accent,
    bold: true,
    align: "center",
  });
  footer(slide, ctx, 12);
  return slide;
}

const SLIDES = {
  1: slide01,
  2: slide02,
  3: slide03,
  4: slide04,
  5: slide05,
  6: slide06,
  7: slide07,
  8: slide08,
  9: slide09,
  10: slide10,
  11: slide11,
  12: slide12,
};

export async function buildSlide(presentation, ctx, number) {
  return SLIDES[number](presentation, ctx);
}
