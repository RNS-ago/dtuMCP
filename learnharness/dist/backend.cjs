// Gives LearnHarness's tutors the dtuMCP tools and answers the DTU Learn view.
// Every call runs server.py's command line once. server.py and tools.json (its
// tool list) sit beside this file; install.py copies them here.
const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const home = os.homedir();
// Started from a desktop entry, LearnHarness does not get the shell's PATH,
// and that is where uv usually lives.
const env = { ...process.env, PATH: [process.env.PATH, `${home}/.local/bin`, `${home}/.cargo/bin`].join(path.delimiter) };
// The same default as DOWNLOADS in server.py.
const downloads = (process.env.DTU_DOWNLOADS || "~/Downloads/dtulearn").replace(/^~(?=$|\/)/, home);
const agents = ["teacher", "practice", "prober"];
// DTU Learn's host, as server.py's DTU_BASE has it, and the session
// server.py's login saved, as it reads them.
const dtuBase = process.env.DTU_BASE || "https://learn.inside.dtu.dk";
const dtuHost = new URL(dtuBase).host;
const cookieFile = (process.env.DTU_COOKIE_FILE || "~/.config/dtumcp/cookie").replace(/^~(?=$|\/)/, home);
const session = () => process.env.DTU_COOKIE?.trim() || (fs.existsSync(cookieFile) ? fs.readFileSync(cookieFile, "utf8").trim() : "");

// What a DTU Learn link points at, when it is something a saved course
// holds: {ou, topic} or {ou, unit}. A short link (quickLink.d2l) names its
// target by an opaque code, so DTU Learn is asked, with the session, where
// it redirects; that is the only request, and only to DTU Learn.
async function dtuTarget(url, signal) {
	let { pathname } = new URL(url);
	if (pathname.endsWith("/quickLink/quickLink.d2l")) {
		const cookie = session();
		if (!cookie) return undefined;
		const response = await fetch(url, { redirect: "manual", headers: { cookie }, signal });
		await response.body?.cancel();
		const location = response.headers.get("location");
		if (!location || new URL(location, url).host !== dtuHost) return undefined;
		pathname = new URL(location, url).pathname;
	}
	// /le/lessons/<ou>/topics/<id>, and a short link's /le/content/<ou>/quicklink/<id>/...
	const topic = /\/d2l\/le\/(?:lessons|content)\/(\d+)\/(?:topics|quicklink|viewContent)\/(\d+)/.exec(pathname);
	if (topic) return { ou: Number(topic[1]), topic: Number(topic[2]) };
	const unit = /\/d2l\/le\/lessons\/(\d+)\/(?:units|lessons)\/(\d+)/.exec(pathname);
	if (unit) return { ou: Number(unit[1]), unit: Number(unit[2]) };
	return undefined;
}

// A file a saved course links to, by the link: a /content/ address, or a
// short link to a course file, which download_course followed and saved.
// The course is the one the address names: /content/enforced/<ou>-..., or ou=.
function linkedFile(url) {
	const { pathname, searchParams, href } = new URL(url);
	const ou = Number(/^\/content\/enforced\/(\d+)-/.exec(pathname)?.[1] ?? searchParams.get("ou"));
	const linked = ou && record(ou)?.linked;
	// A /content/ address is also found by where download_course keeps the
	// file, _linked/ and the same path, whichever link the page used for it.
	const kept = pathname.startsWith("/content/") ? `_linked${decodeURIComponent(pathname.slice("/content".length))}` : undefined;
	for (const [link, saved] of Object.entries(linked ?? {})) {
		if (new URL(link).href === href || saved === kept) return { ou, linked: saved };
	}
	return undefined;
}

// The title of a target's topic or unit in a course's saved tree, if there.
function titleIn(modules, target) {
	for (const module of modules ?? []) {
		if (target.unit === module.ModuleId) return module.Title;
		const topic = (module.Topics ?? []).find(t => t.TopicId === target.topic);
		if (target.topic !== undefined && topic) return topic.Title;
		const found = titleIn(module.Modules, target);
		if (found) return found;
	}
	return undefined;
}

// A web page's readable text: no scripts or styles, a line per block, the
// common entities decoded. ponytail: regex, not a parser; the frame shows the
// real page where the site allows it, so this only has to be readable.
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const pageText = html => html
	.replace(/<!--[\s\S]*?-->/g, " ")
	.replace(/<(script|style|noscript|svg|template|head)\b[\s\S]*?<\/\1>/gi, " ")
	.replace(/<\/?(br|p|div|li|tr|h[1-6]|section|article|header|footer|nav|ul|ol|table|pre|blockquote)\b[^>]*>/gi, "\n")
	.replace(/<[^>]+>/g, " ")
	.replace(/&(#x[\da-f]+|#\d+|\w+);/gi, (entity, name) => name[0] === "#"
		? String.fromCodePoint(Number(name[1].toLowerCase() === "x" ? `0${name.slice(1)}` : name.slice(1)))
		: ENTITIES[name.toLowerCase()] ?? entity)
	.replace(/[ \t ]+/g, " ")
	.replace(/ *\n[ \n]*/g, "\n")
	.trim();

const instructions = `The learner's DTU Learn courses (DTU's Brightspace) are available through the dtu_* tools. Their descriptions name each other without the dtu_ prefix.
- For questions about the learner's DTU courses, material, schedule or deadlines, answer from what these tools return, not from memory. Never invent an org unit, unit or topic id: get them from dtu_list_courses and dtu_course_contents.
- org_unit_id takes the bracketed id or the DTU course number (02231). A course number taken in several terms is ambiguous; the tool lists the offerings, so ask which one unless the context settles it.
- Course material is read from an offline copy. The first read of a course without one downloads it, which takes a minute or two, so say so before that call. dtu_course_contents and dtu_list_announcements give the date the copy was saved; call dtu_download_course when the learner asks for it to be brought up to date or about something newer. dtu_calendar and dtu_list_assignments are always live.
- To find where something is covered, search with dtu_search_courses first: it looks inside every downloaded file and names the PDF page, so dtu_get_pdf_pages can show just that page.
- When you point the learner to course material, add a fenced block with the language dtu-learn to your answer, one line per item: "topic <org_unit_id> <topic_id> pages <first>-<last> | <what to look at>" for a file (pages optional), "unit <org_unit_id> <unit_id> | <what to look at>" for a unit. The learner sees each as a card that opens it in their DTU Learn view, at that page. Use ids the dtu_ tools gave you; never invent them. For example:
\`\`\`dtu-learn
topic 326350 1289393 pages 4-10 | the 3-Sat ≤p DHC construction
unit 326350 1222219 | Week 7's plan
\`\`\`
- Read a unit with dtu_unit_info before its files when asked what a week covers or what to do. Fetch only the topics you need with dtu_get_file; courses have hundreds.
- dtu_get_file returns a file's text, with a PDF's pages headed [Page n]. Figures, diagrams and mathematical notation do not survive extraction: when they matter, look at just those pages with dtu_get_pdf_pages. Each page image costs about 2k tokens, so never ask for a whole document. Spreadsheet text is cell values only. A link returns its URL. A video has no transcript: tell the learner to watch it. dtu_course_contents leaves out topics copied from an earlier year without their files and counts them on their unit's line; if what the learner asks for is not listed and its unit has broken ones, say the material is missing from the course.
- dtu_list_assignments does not say whether the learner submitted. Never claim a submission status.
- If a tool says the session was rejected or has expired, ask the learner to run \`uv run --script server.py login\` in their dtuMCP folder. You cannot do it for them. Downloaded courses can still be read meanwhile.

When what the learner studies comes from one of their DTU courses, that course is the reference:
- Use its definitions, notation and names for things (≤p, DHC, 3-Sat and so on), as its slides and notes write them. Where your usual ones differ, say once how they map, then keep to the course's, so what you build matches what its exercises and exam expect.
- Check against it. When the course states a definition, a theorem or a construction, read it there (dtu_search_courses, then the page) before teaching or posing it; the course's own wording is the first thing to verify against. If the course and your knowledge disagree, say so and show both.
- Graded work is the learner's to do. Hand-ins and quizzes listed by dtu_list_assignments or on the calendar count toward the grade: teach the ideas behind an open one, with hints and comparable problems of your own, but never produce answers the learner could hand in, and never pose its questions as practice. Courses set their own rules on generative AI; when the learner asks for help with graded work, look for them first (dtu_search_courses "generative AI" with the course's org_unit_id) and keep to them.
- If you can research the web, the course's official description, learning objectives and exam form are at https://kurser.dtu.dk/course/<course number>.`;

// What each agent does with the course on top of that.
const roleInstructions = {
	prober: `When the learner's goal is tied to a DTU course (a week's topic, a hand-in, the exam), find where the course is before reasoning out the strands: dtu_calendar for that course shows the lectures and deadlines around now, and the unit for the current week (dtu_unit_info) says what it covers. The course's plan and the material already handed out bound what the lesson can lean on; probe against what the course expects, in its terms, and name the course and the material in the goal you record.`,
	teacher: `When the topic comes from a DTU course, scope it from the course before your own knowledge: read its pages on the topic (dtu_search_courses, then dtu_get_file or dtu_get_pdf_pages). Its real definitions are candidate unconditional truths; its lecture order is evidence of what rests on what; its notation is what the map and every question use. Where it skips a motivation, the method still applies: supply it, then tie it back to how the course puts it. In the approach you present, cite the pages with a dtu-learn block. If dtu_calendar shows a lecture, hand-in or quiz on the topic coming up, mention it so the learner can choose the scope; it does not change the pace. When a step is done, point to the course's own exercises on it (exercise sheets, problem sheets, old exams) with a dtu-learn block.`,
	practice: `When the session's material is a DTU course topic, pose problems in the course's style and notation. Draw on its exercise sheets, problem sheets and old exams (dtu_search_courses "exercise", "exam" and the topic): adapt rather than copy a problem whose solution the course publishes, and check your model solution against the course's suggested solutions where they exist. Never pose a question from an open graded hand-in or quiz. In your closing words after concluding a problem drawn from the course, cite its source with a dtu-learn block.`,
};

// Extensions Windows runs rather than shows when told to open a file, so a
// course's .exe or .js would start on one click. Empty off Windows.
const WINDOWS_RUNS = new Set(process.platform !== "win32" ? [] : [
	...(process.env.PATHEXT ?? "").toLowerCase().split(";").filter(Boolean),
	".lnk", ".url", ".msi", ".hta", ".scr", ".cpl", ".reg", ".jar",
]);

// The id arguments the view sends, checked before they reach a command line.
const id = value => {
	if (!Number.isSafeInteger(value)) throw new Error(`Expected an id, got ${JSON.stringify(value)}`);
	return value;
};

// Each course's newest record, _course-<id>.json, which server.py writes when
// it downloads the course: org unit id -> { file, mtime }.
function copies() {
	const found = new Map();
	if (!fs.existsSync(downloads)) return found;
	for (const dir of fs.readdirSync(downloads, { withFileTypes: true })) {
		if (!dir.isDirectory()) continue;
		for (const name of fs.readdirSync(path.join(downloads, dir.name))) {
			const ou = Number(/^_course-(\d+)\.json$/.exec(name)?.[1]);
			if (!ou) continue;
			const file = path.join(downloads, dir.name, name);
			const { mtimeMs } = fs.statSync(file);
			if (!(found.get(ou)?.mtime > mtimeMs)) found.set(ou, { file, mtime: mtimeMs });
		}
	}
	return found;
}

// An attachment's key, "<news id>/<file id>", as its two ids.
const attachment = key => {
	const ids = /^(\d+)\/(\d+)$/.exec(String(key));
	if (!ids) throw new Error(`Expected "<news id>/<file id>", got ${JSON.stringify(key)}`);
	return [Number(ids[1]), Number(ids[2])];
};

const record = ou => {
	const found = copies().get(ou);
	return found && { ...JSON.parse(fs.readFileSync(found.file, "utf8")), root: path.dirname(found.file) };
};

// A saved course's search entries, the ones download_course writes for its
// offline page's search box (_search.js), each with where it opens in the
// view: {unit}, {topic}, {attachment}, {linked} or {news}. Read again only
// when the course is downloaded again.
// ponytail: every searched course's text stays in memory, a few MB each;
// drop the least recent if many big courses make the app heavy.
const searchCache = new Map(); // org unit id -> { mtime, ou, name, entries }
// The index counts a PDF's page starts in characters, as server.py's Python
// does; JS strings count UTF-16 units, two for a character like 𝑥, so without
// this a page of mathematics pushes every later page's matches onto the next.
function unitStarts(text, starts) {
	let units = 0, chars = 0;
	return starts.map(start => {
		for (; chars < start && units < text.length; chars++) units += text.codePointAt(units) > 0xffff ? 2 : 1;
		return units;
	});
}
function searchCourse(ou, { file, mtime }) {
	const cached = searchCache.get(ou);
	if (cached?.mtime === mtime) return cached;
	const copy = JSON.parse(fs.readFileSync(file, "utf8"));
	let docs = [];
	try {
		// "window.SEARCH = [...];", a script so the offline page can load it from disk.
		const script = fs.readFileSync(path.join(path.dirname(file), "_search.js"), "utf8");
		docs = JSON.parse(script.slice(script.indexOf("=") + 1).trim().replace(/;$/, ""));
	} catch {} // a course saved before the search existed: refreshing it adds one
	// Entries link as the offline page does: a saved file by its path there,
	// a unit or an announcement by its anchor, anything else online.
	const byPath = new Map(), byUrl = new Map(), broken = new Set();
	for (const [key, saved] of Object.entries(copy.files ?? {})) byPath.set(saved, key.includes("/") ? { attachment: key } : { topic: Number(key) });
	for (const saved of Object.values(copy.linked ?? {})) if (!byPath.has(saved)) byPath.set(saved, { linked: saved });
	const walk = modules => {
		for (const module of modules ?? []) {
			for (const topic of module.Topics ?? []) {
				if (topic.Url) byUrl.set(topic.Url, topic.TopicId);
				if (topic.IsBroken) broken.add(topic.TopicId);
			}
			walk(module.Modules);
		}
	};
	walk(copy.modules);
	const target = href => {
		const anchor = /^#([un])(\d+)$/.exec(href);
		if (anchor) return anchor[1] === "u" ? { unit: Number(anchor[2]) } : { news: Number(anchor[2]) };
		const lesson = /\/d2l\/le\/lessons\/\d+\/topics\/(\d+)/.exec(href);
		if (lesson) return { topic: Number(lesson[1]) };
		if (byUrl.has(href)) return { topic: byUrl.get(href) };
		try { return byPath.get(decodeURIComponent(href)); } catch { return undefined; }
	};
	const entries = [];
	for (const doc of docs) {
		const to = target(doc.h);
		if (!to) continue;
		// label: the title and the units above it, so "milk description"
		// finds the Description topic in the Milk case study.
		entries.push({ title: doc.t, where: doc.u ?? "", kind: doc.k, text: doc.x ?? "", pages: doc.p && unitStarts(doc.x ?? "", doc.p), label: `${doc.t} ${doc.u ?? ""}`, target: to, broken: broken.has(to.topic) });
	}
	const course = { mtime, ou, name: copy.name, entries };
	searchCache.set(ou, course);
	return course;
}

// One search word as a pattern, without the i flag, so the case of the text
// can say where a word starts: at the start of a word, or of a hump in a
// joined-up name ("cycle" in "HamiltonCycle"), in any case. A hyphen in the
// word, or between its digits and letters, is optional in the text either
// way: "3SAT" and "3-SAT" find each other. The first letter comes before the
// check of what precedes it, which lets the engine skip ahead to candidates:
// ten times faster than the check first. main.js has the same rule.
const OPTIONAL_HYPHEN = "[-\\u2010]?";
function wordPattern(word) {
	const chars = [...word.replace(/^-+|-+$/g, "")];
	const parts = [];
	chars.forEach((ch, i) => {
		if (ch === "-") return void parts.push(OPTIONAL_HYPHEN);
		const prev = chars[i - 1];
		if (prev && prev !== "-" && /[\p{L}\p{N}]/u.test(prev) && /[\p{L}\p{N}]/u.test(ch) && /\p{N}/u.test(prev) !== /\p{N}/u.test(ch)) parts.push(OPTIONAL_HYPHEN);
		const lower = ch.toLowerCase();
		const upper = ch.toUpperCase();
		parts.push(lower !== upper && [...lower].length === 1 && [...upper].length === 1 ? `[${lower}${upper}]` : ch.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
	});
	const [first = "", ...rest] = parts;
	// \p{L}\p{N} rather than \b, which only knows ASCII and would miss "økonomi".
	return `${first}(?<=(?:(?<![\\p{L}\\p{N}])|(?<=\\p{Ll})(?=\\p{Lu}))${first})${rest.join("")}`;
}

// The page of a PDF entry with the most of the words, then the most matches,
// and the other pages that have as many of the words, at most five.
function bestPages(entry, patterns) {
	const page = offset => entry.pages.filter(start => start <= offset).length;
	const tally = new Map(); // page -> { words: Set, hits }
	patterns.forEach((pattern, i) => {
		for (const found of entry.text.matchAll(new RegExp(pattern.source, "gu"))) {
			const n = page(found.index);
			const t = tally.get(n) ?? tally.set(n, { words: new Set(), hits: 0, first: found.index }).get(n);
			t.words.add(i);
			t.hits++;
			t.first = Math.min(t.first, found.index);
		}
	});
	const ranked = [...tally].sort(([, a], [, b]) => b.words.size - a.words.size || b.hits - a.hits);
	if (!ranked.length) return undefined;
	const [best, top] = ranked[0];
	const also = ranked.slice(1).filter(([, t]) => t.words.size === top.words.size).map(([n]) => n).slice(0, 5).sort((a, b) => a - b);
	return { page: best, at: top.first, also };
}

// Every saved course searched for all of query's words (see wordPattern) in
// an entry's title, its units or its text, as the offline page's search box
// does. Title matches first, then the ones the words occur in most; the first limit are returned, each with the text
// around a match, split so odd parts are matches, and for a PDF the page with
// the most of the words. With partial, after those, entries with some of the
// words, the most first, each saying which it lacks.
function search(query, hideBroken, { only, limit = 100, partial = false } = {}) {
	const words = String(query ?? "").split(/\s+/).filter(word => /[\p{L}\p{N}]/u.test(word)).slice(0, 12);
	if (!words.length) return { total: 0, hits: [] };
	const patterns = words.map(word => new RegExp(wordPattern(word), "u"));
	const found = [];
	for (const [ou, saved] of copies()) {
		if (only !== undefined && ou !== only) continue;
		const course = searchCourse(ou, saved);
		for (const entry of course.entries) {
			if (hideBroken && entry.broken) continue;
			const has = patterns.map(p => p.test(entry.label) || p.test(entry.text));
			const count = has.filter(Boolean).length;
			if (count === words.length || (partial && count)) found.push({ course, entry, has, count, inTitle: patterns.every(p => p.test(entry.label)) });
		}
	}
	const complete = found.filter(f => f.count === words.length).length;
	// Then by how often the words occur, capped so one long book does not
	// bury everything else.
	const often = entry => patterns.reduce((sum, p) => sum + Math.min(20, entry.text.match(new RegExp(p.source, "gu"))?.length ?? 0), 0);
	for (const f of found) f.often = often(f.entry);
	found.sort((a, b) => b.count - a.count || b.inTitle - a.inTitle || b.often - a.often);
	const mark = new RegExp(`(${patterns.map(p => `(?:${p.source})`).join("|")})`, "gu");
	const hits = found.slice(0, limit).map(({ course, entry, has }) => {
		const hit = { ou: course.ou, course: course.name, saved: course.mtime, title: entry.title, where: entry.where, kind: entry.kind, target: entry.target, pageCount: entry.pages?.length ?? 1 };
		if (has.includes(false)) hit.missing = words.filter((_, i) => !has[i]);
		let at = Math.min(...patterns.map(p => entry.text.search(p)).filter(i => i >= 0));
		if (entry.pages && Number.isFinite(at)) {
			const pages = bestPages(entry, patterns);
			hit.page = pages.page;
			if (pages.also.length) hit.also = pages.also;
			at = pages.at;
		}
		if (Number.isFinite(at)) {
			const from = Math.max(0, at - 70);
			hit.snippet = entry.text.slice(from, at + 150).split(mark);
			if (from) hit.snippet[0] = `…${hit.snippet[0]}`;
		}
		return hit;
	});
	return { total: found.length, hits, complete };
}

// The agents' search, dtu_search_courses: the view's search over the saved
// courses, its hits written out with the ids the other dtu_ tools take.
const SEARCH_TOOL = {
	name: "dtu_search_courses",
	label: "DTU Learn: search courses",
	description: "Search the learner's downloaded DTU Learn courses for words: titles, unit text, announcements and the text inside files, a PDF's page by page. Use it to find where something is covered before reading files one by one. Every word must appear; each matches the start of a word or of a part of a joined-up name, so \"reduc\" finds \"reductions\" and \"cycle\" finds \"HamiltonCycle\", and \"3SAT\" and \"3-SAT\" find each other. Course material uses its own terms and abbreviations: when a search comes back thin, try those, or fewer words. When nothing has every word, it returns what has the most of them and says which words each lacks. Returns the best matches, titles first, each with its course, the units above it, its kind, the PDF page with the most matches and other pages with as many words, the ids that dtu_get_file, dtu_unit_info, dtu_get_pdf_pages or dtu_get_announcement_attachment take, and the text around the match. Only downloaded courses are searched; dtu_download_course adds one.",
	parameters: {
		type: "object",
		properties: {
			query: { type: "string", description: "Words to find, e.g. \"dijkstra shortest path\"" },
			org_unit_id: { type: "integer", description: "Only this course: its org unit id, or its DTU course number (02231). Left out, every downloaded course." },
			limit: { type: "integer", description: "How many matches to return, at most 50.", default: 15 },
		},
		required: ["query"],
	},
};

// A course number (at most five digits, DTU's) to the saved course whose name
// starts with it; anything else is taken as an org unit id.
function savedCourse(value) {
	if (value === undefined || value === null || value === "") return undefined;
	const number = Number(value);
	if (!Number.isSafeInteger(number)) throw new Error(`Expected an org unit id or a course number, got ${JSON.stringify(value)}.`);
	if (number > 99999) return number;
	const code = String(number).padStart(5, "0");
	const matches = [...copies().keys()].filter(ou => searchCourse(ou, copies().get(ou)).name?.startsWith(`${code} `));
	if (matches.length === 1) return matches[0];
	throw new Error(matches.length ? `Course ${code} is downloaded for several terms: ${matches.join(", ")}. Pass the org unit id.` : `Course ${code} is not downloaded. Run dtu_list_courses and dtu_download_course.`);
}

// The agents' search: its text, and its hits for the tool's card in the
// transcript (their titles, pages and where each opens).
function searchText(input) {
	const only = savedCourse(input?.org_unit_id);
	const limit = Math.min(50, Math.max(1, Number(input?.limit) || 15));
	const { total, hits, complete } = search(input?.query, true, { only, limit, partial: true });
	const found = { total, hits: hits.map(({ ou, title, kind, page, course, where, target, missing }) => ({ ou, title, kind, page, course, where, target, missing })) };
	if (!total) {
		const saved = [...copies()].map(([ou, file]) => `[${ou}] ${searchCourse(ou, file).name}`);
		return { found, text: saved.length
			? `No matches for "${input?.query}" in ${only ? `course ${only}` : "the downloaded courses"}. Downloaded: ${saved.join("; ")}.`
			: "No course is downloaded yet, so there is nothing to search. dtu_download_course saves one." };
	}
	const ids = (ou, target) => target.topic !== undefined ? `org_unit_id ${ou}, topic_id ${target.topic}`
		: target.unit !== undefined ? `org_unit_id ${ou}, unit_id ${target.unit} (dtu_unit_info)`
		: target.attachment !== undefined ? `org_unit_id ${ou}, announcement attachment ${target.attachment.replace("/", ", file ")} (dtu_get_announcement_attachment)`
		: target.news !== undefined ? `org_unit_id ${ou}, announcement ${target.news} (dtu_list_announcements)`
		: `org_unit_id ${ou}; a file the course's pages link to, not readable with the tools`;
	const shown = total > hits.length ? `; the best ${hits.length} shown` : "";
	const lines = [complete === total ? `${total} match${total === 1 ? "" : "es"}${shown}:`
		: complete ? `${complete} with every word, then ${total - complete} with some of them, each saying which it lacks${shown}:`
		: `Nothing has every word of "${input?.query}". These have the most of them, each saying which it lacks; try the course's own words for those${shown}:`];
	hits.forEach((hit, i) => {
		const pages = hit.page ? `, page ${hit.page}${hit.also ? `; also pages ${hit.also.join(", ")}` : ""}` : "";
		lines.push("", `[${i + 1}] ${hit.title} (${hit.kind}${pages})${hit.missing?.length ? ` — lacks: ${hit.missing.join(", ")}` : ""}`,
			`    ${[hit.course, hit.where].filter(Boolean).join(" › ")}`,
			`    ${ids(hit.ou, hit.target)}`);
		if (hit.snippet) lines.push(`    ${hit.snippet.join("")}`);
	});
	return { found, text: lines.join("\n") };
}

// Words that say nothing about where a statement is covered.
const STOPWORDS = new Set("a an and are as at be by can does each for from has have how if in into is it its of on or that the their then there these this to was what when which with without".split(" "));

// What the learner has studied (LearnHarness's map, its propositions)
// against one course: each proposition found in the course by its name or
// an alias, through the course's search, where every word matters but one
// in four of a long name may be missing. A book that spans the course (over
// 40 pages) says little about which week covers it, so a match in shorter
// material is taken first. The units are the course's top level, in order,
// to show what has nothing studied yet.
// ponytail: a search per name per proposition, cached per course copy; a
// map of thousands would want an index of its own.
const coverageCache = new Map(); // "<ou>:<mtime>:<id>:<name>" -> match or null
function coverage(learning, ou) {
	const saved = copies().get(ou);
	if (!saved) throw new Error("This course has no saved copy. Download it first.");
	const copy = record(ou);
	let graph;
	try { graph = learning?.graph(); } catch {} // no map yet
	const nodes = graph?.propositions ?? graph?.nodes ?? [];
	// Each topic's and unit's top-level unit.
	const top = new Map();
	const walk = (modules, root) => {
		for (const module of modules ?? []) {
			const head = root ?? module.ModuleId;
			top.set(`u${module.ModuleId}`, head);
			for (const topic of module.Topics ?? []) top.set(`t${topic.TopicId}`, head);
			walk(module.Modules, head);
		}
	};
	walk(copy.modules);
	// The name and every alias each find their matches; shorter material
	// from any of them comes first, then the book.
	const find = node => {
		const good = [];
		for (const name of [node.name, ...(node.aliases ?? [])]) {
			const words = String(name ?? "").split(/[\s/,;:()]+/).filter(word => word && !STOPWORDS.has(word.toLowerCase()) && (word.length > 2 || /[\p{Lu}\p{N}]/u.test(word)));
			if (!words.length) continue;
			good.push(...search(words.join(" "), true, { only: ou, limit: 8, partial: true }).hits
				.filter(hit => (hit.missing?.length ?? 0) <= Math.floor(words.length / 4)));
		}
		return good.find(hit => hit.pageCount <= 40) ?? good[0] ?? null;
	};
	const matched = [];
	for (const node of nodes) {
		const key = `${ou}:${saved.mtime}:${node.id}:${node.name}`;
		if (!coverageCache.has(key)) coverageCache.set(key, find(node));
		const hit = coverageCache.get(key);
		if (!hit) continue;
		const unit = hit.target.topic !== undefined ? top.get(`t${hit.target.topic}`) : hit.target.unit !== undefined ? top.get(`u${hit.target.unit}`) : undefined;
		matched.push({ id: node.id, name: node.name, statement: node.statement, unit: unit ?? null, target: hit.target, title: hit.title, page: hit.page });
	}
	return {
		total: nodes.length,
		matched,
		units: (copy.modules ?? []).map(module => ({ id: module.ModuleId, title: module.Title })),
	};
}

exports.activate = ctx => {
	const server = path.join(ctx.directory, "server.py");
	const tools = JSON.parse(fs.readFileSync(path.join(ctx.directory, "tools.json"), "utf8"));

	// One server.py command line; its output, or its error message as the error.
	const run = (args, signal) => new Promise((resolve, reject) => {
		execFile("uv", ["run", "--quiet", "--script", server, ...args.map(String)], {
			env, maxBuffer: 64 << 20, signal: signal ? AbortSignal.any([signal, ctx.signal]) : ctx.signal,
		}, (error, stdout, stderr) => error ? reject(new Error(stderr.trim() || error.message)) : resolve(stdout.trimEnd()));
	});

	// Like run, for a long command that reports progress: each "progress
	// <done> <total>" line it writes to stderr goes to the view as a
	// notification (topic, with data and the two numbers) while it runs.
	const runReporting = (args, topic, data) => new Promise((resolve, reject) => {
		const child = spawn("uv", ["run", "--quiet", "--script", server, ...args.map(String)], {
			env: { ...env, DTU_PROGRESS: "1" }, signal: ctx.signal,
		});
		let out = "", errors = "", partial = "";
		child.stdout.on("data", chunk => { out += chunk; });
		child.stderr.on("data", chunk => {
			const lines = (partial + chunk).split("\n");
			partial = lines.pop();
			for (const line of lines) {
				const progress = /^progress (\d+) (\d+)$/.exec(line);
				if (progress) ctx.events.emit(topic, { ...data, done: Number(progress[1]), total: Number(progress[2]) });
				else errors += `${line}\n`;
			}
		});
		child.on("error", reject);
		child.on("close", code => code === 0 ? resolve(out.trimEnd()) : reject(new Error((errors + partial).trim() || `server.py exited with ${code}`)));
	});

	ctx.registerInstructions({ agents, text: instructions });
	for (const [agent, text] of Object.entries(roleInstructions)) ctx.registerInstructions({ agents: [agent], text });
	ctx.registerTools({
		agents,
		create: () => [{
			...SEARCH_TOOL,
			async execute(_toolCallId, input) {
				const { text, found } = searchText(input);
				return { content: [{ type: "text", text }], details: found };
			},
		},
		// server.py's search_courses is this same search; the one above also gives
		// the tool card its hits, so it stands in for the other.
		...tools.filter(tool => tool.name !== "search_courses").map(tool => ({
			name: `dtu_${tool.name}`,
			label: `DTU Learn: ${tool.name.replaceAll("_", " ")}`,
			description: tool.description,
			parameters: tool.parameters,
			async execute(_toolCallId, input, signal) {
				// The command line takes arguments in order, so a left-out
				// optional one is filled with its default. --json returns MCP
				// content blocks, which have the shape of native ones: text,
				// and page images from get_pdf_pages.
				const props = Object.entries(tool.parameters.properties ?? {});
				const args = props.map(([key, p]) => input?.[key] ?? p.default ?? "");
				// No details: the plugin's card, or else the host's, reads the content.
				return { content: JSON.parse(await run(["--json", tool.name, ...args], signal)), details: undefined };
			},
		}))],
	});

	// The view's calls.
	ctx.handle("courses", async () => {
		// list_courses prints "## <term>" headings over "[<id>] <name>" lines,
		// after a note when DTU Learn could not be reached.
		const saved = copies();
		const terms = [];
		let note = "";
		for (const line of (await run(["list_courses"])).split("\n")) {
			const course = /^\[(\d+)\] (.*)$/.exec(line);
			if (line.startsWith("## ")) terms.push({ name: line.slice(3), courses: [] });
			else if (course && terms.length) terms.at(-1).courses.push({ id: +course[1], name: course[2], saved: saved.get(+course[1])?.mtime });
			else if (!terms.length) note += `${line}\n`;
		}
		return { note: note.trim(), terms };
	});
	ctx.handle("course", input => {
		const copy = record(id(input?.ou));
		// base: what the course's own relative links (/content/..., /d2l/...) are relative to.
		return copy ? { saved: copy.saved, modules: copy.modules, files: copy.files, news: copy.news, videos: copy.videos ?? {}, base: dtuBase } : null;
	});
	ctx.handle("search", input => search(input?.query, !!input?.hideBroken));
	// What a reference in a tutor's answer names: its title and its course's
	// name, from the saved copy, or nothing when the copy lacks it.
	// A tool card's course alone ({ou}) too, and a course number for ou, as
	// the agents may pass one.
	ctx.handle("where", input => {
		let ou;
		try { ou = savedCourse(input?.ou); } catch { return {}; }
		const copy = ou && record(ou);
		if (!copy) return {};
		const target = input?.topic !== undefined ? { topic: id(input.topic) } : input?.unit !== undefined ? { unit: id(input.unit) } : undefined;
		return { ou, course: copy.name, title: target && titleIn(copy.modules, target) };
	});
	// A course page's live tabs, read from DTU Learn as they are opened:
	// {items} for assignments, quizzes, groups or calendar, and the base
	// their relative links resolve against.
	// Coverage is the learner's map against the saved course, read here.
	const TABS = new Set(["assignments", "quizzes", "groups", "calendar"]);
	ctx.handle("course-tab", async input => {
		if (input?.tab === "coverage") return coverage(ctx.learning, id(input.ou));
		if (!TABS.has(input?.tab)) throw new Error(`No tab ${JSON.stringify(input?.tab)}.`);
		return { ...JSON.parse(await run(["course_tab", id(input.ou), input.tab])), base: dtuBase };
	});
	// A unit's text ({ou, unit}), a topic's ({ou, topic}) or an announcement
	// attachment's ({ou, attachment: "<news id>/<file id>"}).
	ctx.handle("text", async input => {
		if (input?.unit !== undefined) return { text: await run(["unit_info", id(input.ou), id(input.unit)]) };
		if (input?.linked !== undefined) {
			return { text: await run(["file_text", savedFile(input)]), file: true };
		}
		if (input?.attachment !== undefined) {
			const [news, file] = attachment(input.attachment);
			return { text: await run(["get_announcement_attachment", id(input.ou), news, file]), file: !!record(input.ou)?.files[input.attachment] };
		}
		return { text: await run(["get_file", id(input?.ou), id(input?.topic)]), file: !!record(input.ou)?.files[input.topic] };
	});
	ctx.handle("download", async input => {
		const summary = await run(["download_course", id(input?.ou)]);
		return { summary, saved: copies().get(input.ou)?.mtime };
	});
	// Where a topic's file is saved in its course's offline copy.
	// A topic's ({ou, topic}), an attachment's ({ou, attachment}) or a file
	// the course links to ({ou, linked: its path in the copy}); a linked path
	// must be one the course's record lists, so nothing else can be named.
	const savedFile = input => {
		const copy = record(id(input?.ou));
		const saved = input.linked !== undefined
			? Object.values(copy?.linked ?? {}).find(path => path === input.linked)
			: input.attachment !== undefined
				? copy?.files[attachment(input.attachment).join("/")]
				: copy?.files[id(input.topic)] ?? copy?.videos?.[input.topic]; // a video downloaded on demand
		if (!saved) throw new Error("That has no saved file. Download the course first.");
		return path.join(copy.root, saved);
	};
	// A video topic: a short-lived link to play it from Brightspace's media
	// service, and whether a copy is saved. When no link can be had, offline
	// say, the error goes back with it, so a saved copy can still be opened.
	ctx.handle("video", async input => {
		const saved = !!record(id(input?.ou))?.videos?.[id(input.topic)];
		try {
			const url = await run(["video_link", input.ou, input.topic]);
			// Its size, for the Download button to say; a HEAD request moves no video.
			let size;
			try {
				const head = await fetch(url, { method: "HEAD", signal: AbortSignal.timeout(10000) });
				size = Number(head.headers.get("content-length")) || undefined;
			} catch {}
			return { url, saved, size };
		} catch (error) {
			return { error: error.message, saved };
		}
	});
	// Downloads a video topic into its course's copy, only when asked: videos
	// are large, so download_course leaves them out.
	// Its progress goes to the view as "video-progress" notifications.
	ctx.handle("download-video", async input => ({
		summary: await runReporting(["download_video", id(input?.ou), id(input.topic)], "video-progress", { ou: input.ou, topic: input.topic }),
	}));
	// The bytes for the view to draw; JSON carries them as base64.
	ctx.handle("pdf", input => {
		const file = savedFile(input);
		if (!file.toLowerCase().endsWith(".pdf")) throw new Error("That topic is not a PDF.");
		return { data: fs.readFileSync(file).toString("base64") };
	});
	// A web address from a page or a PDF the view shows. Only web and mail
	// links: a file: or custom-scheme link from course material would have
	// the system open whatever it names.
	const webAddress = url => {
		const { protocol, href } = new URL(url);
		if (!["http:", "https:", "mailto:"].includes(protocol)) throw new Error(`Not a web address: ${url}`);
		return href;
	};
	// A web page for the view's preview: whether the site lets another app
	// frame it, and its text for when it does not. A DTU Learn page needs
	// the sign-in only the learner's browser has, so it is shown from the
	// saved course when it is a topic or unit of one ({target, title}), and
	// otherwise left to the browser ({browser: why}).
	ctx.handle("preview", async input => {
		const url = webAddress(input?.url);
		const { protocol, host } = new URL(url);
		if (protocol === "mailto:") throw new Error("An e-mail address has no preview.");
		if (host === dtuHost) {
			const file = linkedFile(url);
			if (file) return { url, target: file, title: path.basename(file.linked) };
			const target = await dtuTarget(url, AbortSignal.any([ctx.signal, AbortSignal.timeout(20000)]));
			const title = target && titleIn(record(target.ou)?.modules, target);
			if (title) return { url, target, title };
			return {
				url, browser: target
					? "This leads to a course you have not downloaded, or to something its saved copy does not have. Download or refresh the course to read it here, or open the link in your browser."
					: "This DTU Learn page needs your browser, signed in: it is a quiz, discussion, checklist or another part of DTU Learn itself, not course material.",
			};
		}
		const response = await fetch(url, {
			redirect: "follow",
			headers: { "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36" },
			signal: AbortSignal.any([ctx.signal, AbortSignal.timeout(20000)]),
		});
		const type = response.headers.get("content-type") ?? "";
		// X-Frame-Options DENY or SAMEORIGIN, or a frame-ancestors other
		// than *, keep a page out of any frame but its own site's.
		const ancestors = /frame-ancestors\s+([^;]*)/i.exec(response.headers.get("content-security-policy") ?? "")?.[1].trim();
		const frame = !/deny|sameorigin/i.test(response.headers.get("x-frame-options") ?? "") && (!ancestors || ancestors === "*");
		if (!type.includes("html")) {
			await response.body?.cancel();
			return { url: response.url, frame: false, title: response.url, text: `This link leads to ${type || "a file"}, not a web page. Open it in your browser.` };
		}
		const html = (await response.text()).slice(0, 5 << 20);
		const title = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1];
		return {
			url: response.url, frame, status: response.status,
			title: title ? pageText(title) : response.url,
			text: pageText(html) || "The page has no text; it may need JavaScript. Open it in your browser.",
		};
	});
	// Opens a saved file ({ou, topic}) or a link ({url}) in its usual app.
	ctx.handle("open", async input => {
		const link = typeof input?.url === "string";
		const target = link ? webAddress(input.url) : savedFile(input);
		if (!link && WINDOWS_RUNS.has(path.extname(target).toLowerCase())) {
			throw new Error(`Not opened: Windows would run ${path.basename(target)} as a program. It is saved at ${target}.`);
		}
		// PowerShell reads the target from the environment, so no character in
		// a file name or a link needs quoting for a command line.
		const [command, args] = process.platform === "win32"
			? ["powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", "[Diagnostics.Process]::Start($env:DTU_OPEN)"]]
			: [process.platform === "darwin" ? "open" : "xdg-open", [target]];
		const child = spawn(command, args, { detached: true, stdio: "ignore", windowsHide: true, env: { ...env, DTU_OPEN: target } });
		child.unref();
		await new Promise((resolve, reject) => child.once("spawn", resolve).once("error", reject));
	});
};
