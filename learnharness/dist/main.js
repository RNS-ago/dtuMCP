// The DTU Learn view: courses by term, the units and topics of each downloaded
// course, and the text of whichever one is clicked. backend.cjs supplies it all.
const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';

// pdf.js, imported when the first PDF is shown. Its worker is a module of
// the plugin's own assets, as Rich Code Blocks loads its TeX worker. pdf.js
// ships them as .mjs, renamed .js here: LearnHarness does not serve .mjs as
// JavaScript, and a browser refuses a module served as anything else.
let pdfjsLoad;
const pdfjs = () => pdfjsLoad ??= import(new URL("./assets/pdfjs/pdf.min.js", import.meta.url).href).then(lib => {
	lib.GlobalWorkerOptions.workerSrc = new URL("./assets/pdfjs/pdf.worker.min.js", import.meta.url).href;
	return lib;
}, error => {
	pdfjsLoad = undefined;
	throw error;
});

// The words of a search as one pattern, each found as the search over the
// saved courses finds it (backend.cjs, wordPattern): at the start of a word
// or of a hump in a joined-up name, any case, a hyphen between digits and
// letters optional; undefined for no words. Longest first, so "network" is
// marked whole rather than as "net".
const OPTIONAL_HYPHEN = "[-\\u2010]?";
const wordPattern = word => {
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
	return `${first}(?<=(?:(?<![\\p{L}\\p{N}])|(?<=\\p{Ll})(?=\\p{Lu}))${first})${rest.join("")}`;
};
const wordsPattern = query => {
	const words = String(query ?? "").split(/\s+/).filter(word => /[\p{L}\p{N}]/u.test(word)).sort((a, b) => b.length - a.length);
	if (!words.length) return undefined;
	return new RegExp(words.map(word => `(?:${wordPattern(word)})`).join("|"), "gu");
};

// A course page's tabs, in order; it opens on the first.
const COURSE_TABS = { news: "Announcements", assignments: "Assignments", calendar: "Calendar", groups: "Groups", quizzes: "Quizzes", coverage: "Coverage" };

const UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>';
const DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
const MINUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg>';
const PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';

export function activate(ctx) {
	const tutor = tutorQueue(ctx);
	// Something for the view to open as it mounts: {target}, a reference's.
	const nothing = { target: undefined };
	ctx.registerView({ id: "courses", name: "DTU Learn", mount: (container, signal) => mount(ctx, container, signal, tutor, nothing) });
	ctx.registerAction({ id: "open", name: "DTU Learn", icon: ICON, run: () => ctx.app.openView("courses") });
	ctx.placeAction({ id: "sidebar-open", actionId: "open", location: "sidebar" });

	// The view over a session: the same tree and pane on a surface covering
	// the chat, which closing (its X, Escape, or the button again) uncovers
	// as it was. Opened from the session's top bar, or by a reference card
	// in an answer, at what it names. One at a time.
	let overlay; // { session, close }
	const openOverlay = (surfaceTarget, session, ref) => {
		overlay?.close();
		const closed = new AbortController();
		const content = document.createElement("div");
		content.className = "dtu-learn-overlay";
		const props = { title: "DTU Learn", background: "opaque", content, onClose: () => closed.abort() };
		// Its signal disposes it, whoever closes it.
		if (surfaceTarget) ctx.ui.openSurface(surfaceTarget, props, { signal: closed.signal });
		else ctx.ui.openSurface(props, { signal: closed.signal }); // the page the card was clicked on
		const unmount = mount(ctx, content, closed.signal, tutor, { target: ref, overlay: true });
		const mine = { session, close: () => closed.abort() };
		overlay = mine;
		closed.signal.addEventListener("abort", () => {
			unmount();
			if (overlay === mine) overlay = undefined;
		}, { once: true });
	};
	ctx.registerCodeBlock?.({
		language: "dtu-learn",
		mount: (container, block) => mountRefs(ctx, container, block, ref => openOverlay(undefined, undefined, ref)),
	});
	// Each dtu_ tool call in a transcript as a card saying what the tutor did,
	// with a way to open what it read.
	for (const toolName of Object.keys(TOOL_CARDS)) {
		ctx.registerToolRenderer?.({ toolName, mount: (container, tool) => mountToolCard(ctx, container, tool, ref => openOverlay(undefined, undefined, ref)) });
	}
	ctx.registerAction({
		id: "session-open", icon: ICON,
		// What waits for the tutor shows here too, where the message is written.
		get name() {
			const waiting = tutor.items.length;
			return waiting ? `DTU Learn · ${waiting} for tutor` : "DTU Learn";
		},
		run(context) {
			if (context.surface !== "session") return;
			if (overlay && overlay.session === context.sessionId) return overlay.close();
			openOverlay(context.surfaceTarget, context.sessionId);
		},
	});
	ctx.placeAction({ id: "session-open", actionId: "session-open", location: "top-bar", sessions: ["teaching", "practice"] });
	// What the view queued for the tutor rides on the learner's next message
	// in a teaching or practice session: quotes as text under it, pages as
	// images. Only an ordinary message, not an answer to a question. The
	// queue empties only once the host has accepted that message.
	ctx.registerSubmissionInterceptor?.({
		id: "dtu-learn-material",
		hasPendingInput: target => target.type === "text" && tutor.items.length > 0,
		intercept(input, context) {
			if (input.type !== "text" || !tutor.items.length) return undefined;
			const sent = tutor.items;
			context.onAccepted(() => tutor.remove(sent));
			return {
				type: "text",
				content: {
					text: [input.content.text.trim(), ...sent.map(materialText)].filter(Boolean).join("\n\n"),
					images: [
						...input.content.images,
						...sent.filter(item => item.image).map(item => ({ type: "image", data: item.image, mimeType: "image/png" })),
					],
				},
			};
		},
	});
}

// A ```dtu-learn block in a tutor's answer: a line per piece of course
// material, "topic <org unit id> <topic id> [pages <a>-<b>] [| label]" or
// "unit <org unit id> <unit id> [| label]", each shown as a card with its
// title and course from the saved copy, which opens it in the DTU Learn view
// at its first page. A line that is not one shows as it is.
const REF = /^\s*(topic|unit)\s+(\d+)\s+(\d+)(?:\s+(?:pages?|p\.?)\s*(\d+)(?:\s*[-–]\s*(\d+))?)?\s*(?:\|\s*(.*\S))?\s*$/i;
function parseRef(line) {
	const found = REF.exec(line);
	if (!found) return undefined;
	const [, kind, ou, id, first, last, label] = found;
	return {
		ou: Number(ou), [kind.toLowerCase()]: Number(id),
		...(first && { page: Number(first) }), ...(last && { last: Number(last) }), ...(label && { label }),
	};
}

function mountRefs(ctx, container, block, open) {
	const make = (tag, className, text) => {
		const node = container.ownerDocument.createElement(tag);
		if (className) node.className = className;
		if (text !== undefined) node.textContent = text;
		return node;
	};
	const lines = block.source.split("\n");
	if (block.incomplete) lines.pop(); // the line the tutor is still writing
	const box = make("div", "dtu-learn-refs");
	for (const line of lines) {
		const ref = parseRef(line);
		if (!ref) {
			if (line.trim()) box.append(make("p", "dtu-learn-ref-raw", line));
			continue;
		}
		const card = make("button", "dtu-learn-ref");
		card.type = "button";
		const pages = ref.page ? (ref.last && ref.last !== ref.page ? `pages ${ref.page}–${ref.last}` : `page ${ref.page}`) : "";
		const title = make("strong", undefined, ref.label ?? "…");
		const facts = make("span", "dtu-learn-ref-facts", pages);
		const go = make("span", "dtu-learn-ref-go", "Open in DTU Learn ›");
		const text = make("span", "dtu-learn-ref-text");
		text.append(title, facts);
		card.append(text, go);
		card.title = "Open it in the DTU Learn view";
		card.addEventListener("click", () => open(ref));
		box.append(card);
		// Its real title and course, from the saved copy; the tutor's label
		// says what to look at there.
		ctx.call("where", { ou: ref.ou, topic: ref.topic, unit: ref.unit }).then(where => {
			if (!where?.title) {
				card.disabled = true;
				go.textContent = "Not in your saved courses";
				return;
			}
			title.textContent = where.title;
			facts.textContent = [pages, ref.label, where.course].filter(Boolean).join(" · ");
		}, () => {});
	}
	container.append(box);
	return () => box.remove();
}

// What each dtu_ tool's card says, from its input and what the saved copy
// names (w: {ou, course, title}): [while it runs, once done, what Open shows].
const pagesOf = input => {
	const first = Number(input?.first_page) || 1;
	const last = Number(input?.last_page) || first;
	return last > first ? `pages ${first}–${last}` : `page ${first}`;
};
const quoted = (w, fallback) => w.title ? `“${w.title}”` : fallback;
const courseOf = (w, input) => w.course ?? (input?.org_unit_id ? `course ${input.org_unit_id}` : "a course");
const TOOL_CARDS = {
	dtu_search_courses: (input, w) => {
		const scope = input?.org_unit_id ? courseOf(w, input) : "your courses";
		return [`Searching ${scope} for “${input?.query ?? ""}”`, `Searched ${scope} for “${input?.query ?? ""}”`];
	},
	dtu_get_file: (input, w) => [`Reading ${quoted(w, "a file")}`, `Read ${quoted(w, "a file")}`, { topic: input?.topic_id }],
	dtu_get_pdf_pages: (input, w) => [`Looking at ${pagesOf(input)} of ${quoted(w, "a PDF")}`, `Looked at ${pagesOf(input)} of ${quoted(w, "a PDF")}`, { topic: input?.topic_id, page: Number(input?.first_page) || 1 }],
	dtu_unit_info: (input, w) => [`Reading the unit ${quoted(w, "")}`, `Read the unit ${quoted(w, "")}`, { unit: input?.unit_id }],
	dtu_course_contents: (input, w) => [`Reading the contents of ${courseOf(w, input)}`, `Read the contents of ${courseOf(w, input)}`, {}],
	dtu_list_announcements: (input, w) => [`Reading the announcements of ${courseOf(w, input)}`, `Read the announcements of ${courseOf(w, input)}`, {}],
	dtu_get_announcement_attachment: (input, w) => [`Reading an announcement's attachment in ${courseOf(w, input)}`, `Read an announcement's attachment in ${courseOf(w, input)}`, {}],
	dtu_list_assignments: (input, w) => [`Checking the assignments of ${courseOf(w, input)}`, `Checked the assignments of ${courseOf(w, input)}`, {}],
	dtu_calendar: (input, w) => {
		const scope = `the next ${Number(input?.days) || 7} days${input?.org_unit_id ? ` of ${courseOf(w, input)}` : ""}`;
		return [`Checking the calendar for ${scope}`, `Checked the calendar for ${scope}`];
	},
	dtu_list_courses: () => ["Listing your DTU Learn courses", "Listed your DTU Learn courses"],
	dtu_download_course: (input, w) => [`Downloading ${courseOf(w, input)}`, `Downloaded ${courseOf(w, input)}`, {}],
	dtu_download_term: input => [`Downloading the courses of ${input?.term || "this term"}`, `Downloaded the courses of ${input?.term || "this term"}`],
};

function mountToolCard(ctx, container, tool, open) {
	const make = (tag, className, text) => {
		const node = container.ownerDocument.createElement(tag);
		if (className) node.className = className;
		if (text !== undefined) node.textContent = text;
		return node;
	};
	const running = tool.state === "input-streaming" || tool.state === "input-available";
	const failed = tool.state === "output-error" || !!tool.result?.isError;
	const content = tool.result?.content ?? [];
	const text = content.filter(block => block.type === "text").map(block => block.text).join("\n").trim();
	const card = make("div", `dtu-learn-tool${running ? " running" : ""}${failed ? " failed" : ""}`);
	const head = make("div", "dtu-learn-tool-head");
	const icon = make("span", "dtu-learn-tool-icon");
	icon.innerHTML = ICON; // the plugin's own trusted icon
	const what = make("span", "dtu-learn-tool-what", tool.toolName.replace(/^dtu_/, "").replaceAll("_", " "));
	head.append(icon, what);
	card.append(head);
	container.append(card);
	const input = tool.input ?? {};
	// Names from the saved copy; an org unit id or course number either way.
	const ask = input.org_unit_id ? ctx.call("where", { ou: input.org_unit_id, topic: input.topic_id, unit: input.unit_id }).catch(() => ({})) : Promise.resolve({});
	ask.then(w => {
		if (tool.signal.aborted) return;
		const [doing, done, ref] = TOOL_CARDS[tool.toolName](input, w ?? {});
		const total = (tool.output ?? tool.result?.details)?.total;
		what.textContent = running ? `${doing}…` : failed ? `${doing}: failed`
			: Number.isInteger(total) ? `${done} · ${total === 1 ? "1 match" : `${total} matches`}` : done;
		if (ref && w?.ou && !running && !failed) {
			const go = make("button", "dtu-learn-action", "Open in DTU Learn");
			go.type = "button";
			go.title = "Open it over the chat, where the tutor found it";
			go.addEventListener("click", () => open({ ou: w.ou, ...ref }));
			head.append(go);
		}
		// The pages the tutor looked at, each opening at itself.
		const images = content.filter(block => block.type === "image");
		if (images.length && !failed) {
			const strip = make("div", "dtu-learn-tool-pages");
			const first = Number(input.first_page) || 1;
			images.forEach((image, i) => {
				const page = make("button", "dtu-learn-tool-page");
				page.type = "button";
				const img = make("img");
				img.alt = `Page ${first + i}`;
				img.src = `data:${image.mimeType};base64,${image.data}`;
				img.addEventListener("error", () => img.remove()); // a host that refuses data: pictures
				page.append(img, make("span", undefined, `p. ${first + i}`));
				if (w?.ou) page.addEventListener("click", () => open({ ou: w.ou, topic: input.topic_id, page: first + i }));
				else page.disabled = true;
				strip.append(page);
			});
			card.insertBefore(strip, card.children[1] ?? null);
		}
	});
	if (failed) {
		card.append(make("p", "dtu-learn-error", tool.errorText || text || "The tool failed."));
		return () => card.remove();
	}
	if (running) return () => card.remove();
	// A search's matches, each opening where it is.
	const found = tool.output?.hits ?? tool.result?.details?.hits;
	if (Array.isArray(found) && found.length) {
		const list = make("div", "dtu-learn-tool-hits");
		found.slice(0, 6).forEach(hit => {
			const row = make("button", "dtu-learn-tool-hit");
			row.type = "button";
			row.append(make("strong", undefined, hit.title),
				// The course only when the search went over several.
				make("span", undefined, [hit.page && `p. ${hit.page}`, !input.org_unit_id && hit.course, hit.where, hit.missing?.length && `lacks ${hit.missing.join(", ")}`].filter(Boolean).join(" · ")));
			row.addEventListener("click", () => open({ ou: hit.ou, target: hit.target, title: hit.title, page: hit.page }));
			list.append(row);
		});
		if (found.length > 6) list.append(make("p", "dtu-learn-tool-more", `and ${found.length - 6} more in the result below`));
		card.append(list);
	}
	// What the tool returned, as the tutor got it.
	if (text) {
		const fold = make("details", "dtu-learn-tool-text");
		fold.append(make("summary", undefined, "What the tutor got"), make("pre", undefined, text));
		card.append(fold);
	}
	return () => card.remove();
}

// Course material waiting for the learner's next message: {id, title,
// course, page?, ou?, topic?} with text, a quote, or image, a page as PNG
// base64. Kept for the window's life, not saved; the view shows and edits it.
function tutorQueue(ctx) {
	let items = [];
	let next = 0;
	const listeners = new Set();
	const changed = () => {
		ctx.refreshSubmissions?.(); // the session's Send can now go with no text of its own
		ctx.refreshActions?.(); // and its top bar says how much waits
		for (const listener of listeners) listener(items);
	};
	return {
		get items() { return items; },
		add(item) {
			items = [...items, { ...item, id: ++next }];
			changed();
		},
		remove(gone) {
			const ids = new Set(gone.map(item => item.id));
			items = items.filter(item => !ids.has(item.id));
			changed();
		},
		listen(listener, signal) {
			listeners.add(listener);
			signal.addEventListener("abort", () => listeners.delete(listener), { once: true });
		},
	};
}

// How a queued item reads in the message: where it is from, ids the tutor's
// dtu_ tools take, then the quote, or a note that the page is attached.
function materialText(item) {
	const where = [item.course, item.ou && `org unit ${item.ou}`, item.topic && `topic ${item.topic}`].filter(Boolean).join("; ");
	const from = `From DTU Learn, ${item.title}${item.page ? `, page ${item.page}` : ""}${where ? ` (${where})` : ""}`;
	return item.image ? `${from}: the page is attached as an image.`
		: `${from}:\n${item.text.split("\n").map(line => `> ${line}`).join("\n")}`;
}

function mount(ctx, container, signal, tutor, opener) {
	const el = (tag, className, text) => {
		const node = container.ownerDocument.createElement(tag);
		if (className) node.className = className;
		if (text !== undefined) node.textContent = text;
		return node;
	};
	const root = el("div", "dtu-learn");
	if (opener.overlay) root.classList.add("dtu-learn-in-overlay");
	const left = el("div", "dtu-learn-tree");
	const note = el("p", "dtu-learn-muted", "Loading courses…");
	const treeBox = el("div", "dtu-learn-tree-scroll"); // the tree scrolls in here, below the checkbox
	const pane = el("div", "dtu-learn-pane");
	// Broken topics were copied from an earlier year without their files, so
	// they are hidden unless unticked. The choice is remembered per browser;
	// storage may be refused.
	let hideBroken = true;
	try { hideBroken = localStorage.getItem("dtu-learn:hide-broken") !== "0"; } catch {}
	const toggle = el("label", "dtu-learn-toggle");
	const box = el("input");
	box.type = "checkbox";
	box.checked = hideBroken;
	box.addEventListener("change", () => {
		hideBroken = box.checked;
		try { localStorage.setItem("dtu-learn:hide-broken", hideBroken ? "1" : "0"); } catch {}
		redraw();
		find();
	});
	toggle.append(box, "Hide broken topics");
	// The saved courses' search, as on their offline pages: titles, units and
	// the text inside files. Its results take the tree's place while the box
	// has text; "/" goes to it.
	const search = el("input", "dtu-learn-search");
	search.type = "search";
	search.placeholder = "Search saved courses, inside files too (press /)";
	search.setAttribute("aria-label", "Search the saved courses");
	const results = el("div", "dtu-learn-tree-scroll dtu-learn-results");
	results.setAttribute("aria-live", "polite");
	results.hidden = true;
	left.append(note, search, toggle, treeBox, results);
	pane.append(el("p", "dtu-learn-muted", "Pick a unit or a topic to read it here."));
	// The bar between the tree and the pane: drag it, or focus it and use the
	// arrow keys, to share the width differently; a double click puts it
	// back. The tree's share is remembered per browser, like the checkbox.
	const bar = el("div", "dtu-learn-split");
	bar.setAttribute("role", "separator");
	bar.setAttribute("aria-orientation", "vertical");
	bar.setAttribute("aria-label", "Width of the course tree");
	bar.tabIndex = 0;
	root.append(left, bar, pane);
	container.append(root);
	let share; // the tree's percentage of the width; undefined is the default
	const split = value => {
		share = value;
		if (share === undefined) root.style.removeProperty("--dtu-learn-left");
		else root.style.setProperty("--dtu-learn-left", `${share}%`);
		bar.setAttribute("aria-valuenow", String(Math.round(share ?? 40)));
	};
	const keep = () => {
		try {
			if (share === undefined) localStorage.removeItem("dtu-learn:split");
			else localStorage.setItem("dtu-learn:split", String(share));
		} catch {}
	};
	// The share that ends the tree at clientX, leaving the tree 15rem and the
	// pane 20rem at least.
	const shareAt = clientX => {
		const box = root.getBoundingClientRect();
		const style = getComputedStyle(root);
		const inner = box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
		const rem = parseFloat(getComputedStyle(container.ownerDocument.documentElement).fontSize);
		const x = clientX - box.left - parseFloat(style.paddingLeft);
		return Math.min(Math.max(x, 15 * rem), inner - 22 * rem) / inner * 100;
	};
	split(undefined);
	try { const kept = Number(localStorage.getItem("dtu-learn:split")); if (kept > 0) split(kept); } catch {}
	let grip = 0; // how far right of the tree's edge the bar was grabbed
	bar.addEventListener("pointerdown", event => {
		event.preventDefault();
		grip = event.clientX - left.getBoundingClientRect().right;
		bar.setPointerCapture(event.pointerId);
		root.classList.add("dtu-learn-resizing");
	}, { signal });
	bar.addEventListener("pointermove", event => {
		if (bar.hasPointerCapture(event.pointerId)) split(shareAt(event.clientX - grip));
	}, { signal });
	bar.addEventListener("lostpointercapture", () => {
		root.classList.remove("dtu-learn-resizing");
		keep();
	}, { signal });
	bar.addEventListener("dblclick", () => { split(undefined); keep(); }, { signal });
	bar.addEventListener("keydown", event => {
		const step = { ArrowLeft: -2, ArrowRight: 2 }[event.key];
		if (!step) return;
		event.preventDefault();
		split(shareAt(left.getBoundingClientRect().right + step / 100 * root.clientWidth));
		keep();
	}, { signal });

	// The view fills the window from where it starts down to the bottom, so
	// the app has nothing to scroll and the tree and the pane each scroll on
	// their own. Measured rather than guessed: the app's top bar is above it.
	const win = container.ownerDocument.defaultView;
	const fit = () => root.style.setProperty("--dtu-learn-height", `${Math.max(240, win.innerHeight - root.getBoundingClientRect().top)}px`);
	fit();
	win.requestAnimationFrame(fit); // again once the app has laid the view out
	win.addEventListener("resize", fit, { signal });

	let terms = [];
	let widget;
	let zoom = 1; // PDF page width as a share of the pane's; kept between PDFs
	const open = new Set(); // row keys of expanded rows
	const courses = new Map(); // org unit id -> its copy, null while loading, or an Error
	// Listeners for the backend's "video-progress" notifications.
	const videoProgress = new Set();
	signal.addEventListener("abort", ctx.onNotification(event => {
		if (event.topic === "video-progress") for (const listener of videoProgress) listener(event.data);
	}), { once: true });
	const cache = new Map(); // row key -> its content element
	const redraw = () => widget?.update({ rows: rows() });
	const day = ms => new Date(ms).toLocaleDateString();

	// Row content must keep its identity across updates, or the tree remounts it.
	const once = (key, make) => cache.get(key) ?? cache.set(key, make()).get(key);
	const button = (className, label, onClick) => {
		const node = el("button", className, label);
		node.type = "button";
		node.addEventListener("click", onClick);
		return node;
	};
	const fold = (key, label, onOpen) => ({
		label, expanded: open.has(key),
		onChange(expanded) {
			if (expanded) open.add(key); else open.delete(key);
			if (expanded) onOpen?.();
			redraw();
		},
	});

	const rows = () => terms.map(term => ({
		key: `t:${term.name}`, content: term.name,
		disclosure: fold(`t:${term.name}`, term.name),
		children: term.courses.map(courseRow),
	}));

	function courseRow(course) {
		const key = `c:${course.id}`;
		return {
			key, content: once(key, () => courseCell(course)),
			cells: { info: course.saved ? `saved ${day(course.saved)}` : "" },
			...(course.saved && { disclosure: fold(key, course.name, () => load(course.id)), children: courseChildren(course.id) }),
		};
	}

	function courseCell(course) {
		const cell = el("span", "dtu-learn-course");
		const action = button("dtu-learn-action", course.saved ? "Refresh" : "Download", () => download(course, action));
		cell.append(button("dtu-learn-link", course.name, () => openCourse(course)), action);
		return cell;
	}

	// A course's name opens its tree and shows its announcements beside it.
	function openCourse(course) {
		if (course.saved) {
			open.add(`c:${course.id}`);
			load(course.id);
			redraw();
		}
		mark(`c:${course.id}`);
		main.showCourse(course);
	}

	// The tree marks the row of what the pane shows: a course's name, or a
	// unit's or a topic's title.
	let current;
	const label = key => {
		const node = cache.get(key);
		return node?.matches(".dtu-learn-link") ? node : node?.querySelector(".dtu-learn-link");
	};
	const mark = key => {
		label(current)?.classList.remove("dtu-learn-current");
		current = key;
		label(key)?.classList.add("dtu-learn-current");
	};

	// The units from the top of a course down to a unit, or to the one
	// holding a topic.
	const pathTo = (modules, target, above = []) => {
		for (const module of modules ?? []) {
			const here = [...above, module.ModuleId];
			if (target.unit === module.ModuleId || (module.Topics ?? []).some(t => t.TopicId === target.topic)) return here;
			const found = pathTo(module.Modules, target, here);
			if (found) return found;
		}
		return undefined;
	};

	// Typing searches, a moment after the last key; a later search wins over
	// one still answering. An empty box brings the tree back, scrolled to
	// where the last result opened.
	let asked = 0;
	let pending;
	function find() {
		clearTimeout(pending);
		const mine = ++asked;
		const query = search.value.trim();
		if (!query) {
			results.replaceChildren();
			if (results.hidden) return;
			results.hidden = true;
			treeBox.hidden = false;
			win.requestAnimationFrame(() => label(current)?.scrollIntoView({ block: "nearest" }));
			return;
		}
		treeBox.hidden = true;
		results.hidden = false;
		pending = setTimeout(async () => {
			let answer;
			try {
				answer = await ctx.call("search", { query, hideBroken });
			} catch (error) {
				answer = error;
			}
			if (mine === asked && !signal.aborted) list(answer, query);
		}, 120);
	}
	search.addEventListener("input", find, { signal });
	search.addEventListener("keydown", event => {
		if (event.key === "Enter") results.querySelector(".dtu-learn-link")?.click();
	}, { signal });
	// Escape leaves the search for the tree from anywhere in the view: the
	// box, a result, the pane. Not from another text field, which has its own
	// use for it (a PDF's find box), nor from a video filling the window, a
	// preview or the rest of the app.
	win.addEventListener("keydown", event => {
		if (event.key !== "Escape" || results.hidden || !root.getClientRects().length) return;
		const target = event.target;
		if (target !== container.ownerDocument.body && !root.contains(target)) return;
		if (target !== search && target.closest?.('input:not([type="checkbox"]), textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
		if (container.ownerDocument.querySelector(".dtu-learn-fill")) return;
		event.preventDefault();
		event.stopPropagation(); // the view's Escape, not the app's
		search.value = "";
		search.blur(); // so "/" comes back to it
		find();
	}, { capture: true, signal });
	// "/" goes to the box from anywhere in the view, as on the offline pages,
	// unless a text field has the keys or the view is not the one showing.
	win.addEventListener("keydown", event => {
		if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || !root.getClientRects().length) return;
		const target = event.target;
		if (target !== container.ownerDocument.body && !root.contains(target)) return; // the app's, or a preview's
		if (target.closest?.('input:not([type="checkbox"]), textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
		event.preventDefault();
		search.focus();
		search.select();
	}, { capture: true, signal });

	function list(answer, query) {
		if (answer instanceof Error) {
			results.replaceChildren(el("p", "dtu-learn-error", answer.message));
			return;
		}
		const { total, hits } = answer;
		const count = el("p", "dtu-learn-muted", !total ? "No results. Only downloaded courses are searched."
			: total > hits.length ? `${total} results, the first ${hits.length} shown` : `${total} result${total === 1 ? "" : "s"}`);
		const items = el("ul", "dtu-learn-hits");
		for (const hit of hits) {
			// The whole result opens it. Its title is the button, for the
			// keyboard; its click reaches the item too. A drag that selects
			// text, to copy it, opens nothing.
			const item = el("li");
			item.addEventListener("click", () => {
				if (!container.ownerDocument.getSelection()?.isCollapsed) return;
				go(hit, query);
			});
			const title = el("button", "dtu-learn-link", hit.title);
			title.type = "button";
			const head = el("div", "dtu-learn-hit");
			head.append(title, el("span", "dtu-learn-tag", hit.page ? `${hit.kind} · p. ${hit.page}` : hit.kind));
			item.append(head, el("p", "dtu-learn-where", [hit.course, hit.where].filter(Boolean).join(" › ")));
			if (hit.snippet) {
				const text = el("p", "dtu-learn-snippet");
				hit.snippet.forEach((part, i) => text.append(i % 2 ? el("mark", undefined, part) : part));
				item.append(text);
			}
			items.append(item);
		}
		results.replaceChildren(count, items);
	}

	// A result opens in the pane with the search's words marked, a PDF at
	// the page they are on, and the tree opens down to it, so leaving the
	// search shows where it sits. The results stay, to open the next one.
	async function go({ ou, course: name, saved, title, target, page }, query) {
		const term = terms.find(t => t.courses.some(c => c.id === ou));
		const course = term?.courses.find(c => c.id === ou) ?? { id: ou, name, saved };
		course.saved ??= saved; // downloaded by a tutor since the list was read
		if (term) open.add(`t:${term.name}`);
		open.add(`c:${ou}`);
		await load(ou); // the pane needs the copy to tell a PDF or a video
		const copy = courses.get(ou);
		for (const unit of (copy && !(copy instanceof Error) && pathTo(copy.modules, target)) || []) open.add(`u:${ou}:${unit}`);
		redraw();
		mark(target.topic !== undefined ? `p:${ou}:${target.topic}` : target.unit !== undefined ? `u:${ou}:${target.unit}` : `c:${ou}`);
		if ("news" in target) main.showCourse(course, target.news ?? undefined, query); // a course page
		else main.show(title, { ou, ...target, ...(page && { page }), find: query });
	}

	// Wraps each match of pattern in node's text in a mark, and returns the
	// marks in reading order. The text is matched a text node at a time, which
	// for a PDF is a text item at a time, as its find counts them.
	const markWords = (node, pattern) => {
		const marks = [];
		if (!pattern) return marks;
		const walker = container.ownerDocument.createTreeWalker(node, NodeFilter.SHOW_TEXT);
		const texts = [];
		while (walker.nextNode()) texts.push(walker.currentNode);
		for (const text of texts) {
			const parts = [];
			let last = 0;
			for (const found of text.data.matchAll(pattern)) {
				const mark = el("mark", "dtu-learn-mark", found[0]);
				marks.push(mark);
				parts.push(text.data.slice(last, found.index), mark);
				last = found.index + found[0].length;
			}
			if (parts.length) text.replaceWith(...parts, text.data.slice(last));
		}
		return marks;
	};
	const unmark = node => {
		for (const mark of node.querySelectorAll("mark.dtu-learn-mark")) mark.replaceWith(mark.textContent);
		node.normalize(); // one text node per PDF text item again
	};

	function courseChildren(ou) {
		const copy = courses.get(ou);
		if (!copy || copy instanceof Error) return [{ key: `l:${ou}`, content: copy?.message ?? "Loading…" }];
		const unit = m => {
			const key = `u:${ou}:${m.ModuleId}`;
			const topics = (m.Topics ?? []).filter(t => !(hideBroken && t.IsBroken));
			const children = [...(m.Modules ?? []).map(unit), ...topics.map(topic)];
			return {
				// A unit's name opens its branch and shows its own text beside it.
				key, content: once(key, () => button("dtu-learn-link", m.Title, () => {
					open.add(key);
					redraw();
					mark(key);
					main.show(m.Title, { ou, unit: m.ModuleId });
				})),
				...(children.length && { disclosure: fold(key, m.Title), children }),
			};
		};
		const topic = t => {
			const key = `p:${ou}:${t.TopicId}`;
			return {
				key, content: once(key, () => button("dtu-learn-link", t.Title, () => {
					mark(key);
					main.show(t.Title, { ou, topic: t.TopicId });
				})),
				cells: { info: kind(t, copy) },
			};
		};
		return copy.modules.map(unit);
	}

	function kind(topic, copy) {
		if (topic.IsBroken) return "broken";
		const saved = copy.files[topic.TopicId];
		if (saved) return /\.(\w+)$/.exec(saved)?.[1].toUpperCase() ?? "file";
		if (topic.TypeIdentifier === "ContentService") return copy.videos?.[topic.TopicId] ? "video, saved" : "video";
		return { File: "not saved" }[topic.TypeIdentifier] ?? "link";
	}

	// Reads a course's copy once; a second caller waits on the same read.
	const loads = new Map(); // org unit id -> the read in progress or done
	function load(ou) {
		if (!loads.has(ou)) loads.set(ou, (async () => {
			courses.set(ou, null);
			try {
				courses.set(ou, await ctx.call("course", { ou }) ?? new Error("No offline copy. Download the course."));
			} catch (error) {
				courses.set(ou, error);
			}
			if (!signal.aborted) redraw();
		})());
		return loads.get(ou);
	}

	// A click on a link previews its page inside the app; Ctrl+click (Cmd on
	// a Mac) or a middle click opens it in the system's browser, through the
	// backend as saved files are, rather than leaving it to the app what a
	// click does. Mail links always go to the mail program.
	const openUrl = url => ctx.call("open", { url }).catch(error => console.error("dtu-learn: could not open", url, error));
	const anchor = (url, text = url) => {
		const link = el("a", undefined, text);
		link.href = url;
		link.addEventListener("click", event => {
			event.preventDefault();
			if (event.ctrlKey || event.metaKey || url.startsWith("mailto:")) openUrl(url);
			else preview(url);
		});
		link.addEventListener("auxclick", event => {
			if (event.button !== 1) return;
			event.preventDefault();
			openUrl(url);
		});
		return link;
	};

	// A link's page on a surface over the view, so closing it (its X, or
	// Escape) comes back to exactly where the link was.
	function preview(url) {
		const page = el("div", "dtu-learn-preview");
		const closed = new AbortController();
		const life = AbortSignal.any([signal, closed.signal]);
		ctx.ui.openSurface({ title: `Preview of ${url}`, content: page, onClose: () => closed.abort() }, { signal: life });
		panel(page, life).showWeb(url);
	}

	// Text with its web addresses made links. A sentence's full stop or
	// closing bracket after an address is not part of it.
	const linkify = (node, text) => {
		let last = 0;
		for (const found of text.matchAll(/https?:\/\/[^\s<>"']*[^\s<>"'.,;:!?)\]]/g)) {
			node.append(text.slice(last, found.index), anchor(found[0]));
			last = found.index + found[0].length;
		}
		node.append(text.slice(last));
		return node;
	};

	// D2L's rich text, a unit's or an announcement's, as DOM that is safe to
	// show: parsed inert, then rebuilt from a short list of tags with no
	// attributes. Links become the view's own links, relative ones resolved
	// against DTU Learn (base); a picture becomes its description. Scripts,
	// styles, frames, forms and every attribute are left behind.
	const RICH = new Set(["P", "DIV", "SPAN", "BR", "HR", "UL", "OL", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "STRONG", "B", "EM", "I", "U", "S", "SUB", "SUP", "SMALL", "CODE", "PRE", "BLOCKQUOTE", "TABLE", "THEAD", "TBODY", "TR", "TD", "TH"]);
	const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "NOSCRIPT", "SVG", "FORM", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "HEAD", "TITLE"]);
	const rich = (html, base) => {
		const out = el("div", "dtu-learn-rich");
		const rebuild = (from, to, inLink) => {
			for (const node of from.childNodes) {
				if (node.nodeType === 3) { // text
					if (inLink) to.append(node.textContent);
					else linkify(to, node.textContent);
				} else if (node.nodeType !== 1 || DROP.has(node.tagName)) {
					continue;
				} else if (node.tagName === "A") {
					let url;
					try { url = new URL(node.getAttribute("href") ?? "", base).href; } catch {}
					const link = !inLink && url && /^(https?|mailto):/.test(url) ? anchor(url, "") : el("span");
					rebuild(node, link, true);
					if (!link.textContent.trim()) {
						// A link round a picture with no description would be empty,
						// so nothing to click: it gets a word. A link round nothing at
						// all, an editing leftover, shows nothing on DTU Learn either.
						if (!node.querySelector("img")) continue;
						link.textContent = "(link)";
					}
					to.append(link);
				} else if (node.tagName === "IMG") {
					const alt = node.getAttribute("alt")?.trim();
					if (alt) to.append(`[${alt}]`);
				} else if (RICH.has(node.tagName)) {
					const copy = el(node.tagName.toLowerCase());
					rebuild(node, copy, inLink);
					to.append(copy);
				} else {
					rebuild(node, to, inLink); // any other wrapper: its content only
				}
			}
		};
		rebuild(new DOMParser().parseFromString(html, "text/html").body, out, false);
		return out;
	};
	const findTopic = (modules, id) => {
		for (const module of modules ?? []) {
			const topic = (module.Topics ?? []).find(t => t.TopicId === id) ?? findTopic(module.Modules, id);
			if (topic) return topic;
		}
		return undefined;
	};
	const findUnit = (modules, id) => {
		for (const module of modules ?? []) {
			if (module.ModuleId === id) return module;
			const found = findUnit(module.Modules, id);
			if (found) return found;
		}
		return undefined;
	};

	// A topic's file is saved under its id, an announcement's attachment
	// under "<news id>/<file id>", a file the course links to at its path.
	const isPdf = ({ ou, topic, attachment, linked }) => /\.pdf$/i.test(linked ?? courses.get(ou)?.files?.[attachment ?? topic] ?? "");

	// The live tabs of a course page, read once each while the view is open,
	// unless asked for again; a failed read is tried again next time.
	const tabCache = new Map(); // "<ou>:<tab>" -> the read, with when it was made
	const tabData = (ou, tab) => {
		const key = `${ou}:${tab}`;
		if (!tabCache.has(key)) {
			tabCache.set(key, ctx.call("course-tab", { ou, tab }).then(
				data => ({ ...data, read: new Date().toISOString() }),
				error => { tabCache.delete(key); throw error; },
			));
		}
		return tabCache.get(key);
	};

	// Dates as the learner's system writes them, and how far off they are.
	const dateTime = iso => new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
	const timeOf = iso => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
	const relative = iso => {
		const hours = (Date.parse(iso) - Date.now()) / 36e5;
		const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
		return Math.abs(hours) < 24 ? format.format(Math.round(hours), "hour") : format.format(Math.round(hours / 24), "day");
	};
	const later = iso => iso && Date.parse(iso) > Date.now();
	// Upcoming first, the soonest at the top, then those with no date, then
	// past ones, the latest first. Each with whether it is past.
	const ordered = (items, dateOf) => {
		const at = item => dateOf(item) ? Date.parse(dateOf(item)) : undefined;
		const now = Date.now();
		return [
			...items.filter(item => at(item) >= now).sort((a, b) => at(a) - at(b)),
			...items.filter(item => at(item) === undefined),
			...items.filter(item => at(item) < now).sort((a, b) => at(b) - at(a)),
		].map(item => ({ item, past: at(item) < now }));
	};
	// D2L rich text: its HTML, for its links, else its plain text; nothing
	// when it has neither.
	const richField = (field, base) => field?.html?.trim() ? rich(field.html, base)
		: field?.text?.trim() ? linkify(el("div", "dtu-learn-text"), field.text.trim()) : undefined;
	// A card of a live tab: its title, beside it a button to open it on DTU
	// Learn in the browser, and a line of facts under it. A past one is dimmed.
	const card = (title, url, facts, past) => {
		const item = el("article");
		if (past) item.classList.add("dtu-learn-past");
		const top = el("div", "dtu-learn-card-head");
		top.append(el("h3", undefined, title));
		if (url) {
			const open = button("dtu-learn-action", "Open on DTU Learn", () => openUrl(url));
			open.title = "Open it in your browser, signed in to DTU Learn";
			top.append(open);
		}
		item.append(top, el("p", "dtu-learn-muted", facts.filter(Boolean).join(" · ")));
		return item;
	};
	const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
	// Each live tab's page from what course_tab read.
	const LIVE_TABS = {
		assignments({ items, base }) {
			const body = el("div", "dtu-learn-cards");
			if (!items.length) body.append(el("p", undefined, "No assignments."));
			for (const { item: a, past } of ordered(items, a => a.due ?? a.end)) {
				const article = card(a.name, a.url, [
					a.due ? `Due ${dateTime(a.due)}, ${relative(a.due)}` : "No due date",
					a.end && a.end !== a.due && `closes ${dateTime(a.end)}`,
					later(a.start) && `opens ${dateTime(a.start)}`,
					a.group && "group hand-in",
					a.points && `${a.points} points`,
				], past);
				const text = richField(a.instructions, base);
				if (text) article.append(text);
				if (a.files.length) article.append(el("p", "dtu-learn-attachment", `Attached on DTU Learn: ${a.files.join(", ")}`));
				for (const link of a.links) {
					let url;
					try { url = new URL(link.url, base).href; } catch { continue; }
					const line = el("p", "dtu-learn-attachment", "Link: ");
					line.append(anchor(url, link.name));
					article.append(line);
				}
				body.append(article);
			}
			return body;
		},
		quizzes({ items, base }) {
			const body = el("div", "dtu-learn-cards");
			if (!items.length) body.append(el("p", undefined, "No quizzes."));
			for (const { item: q, past } of ordered(items, q => q.due ?? q.end)) {
				const article = card(q.name, q.url, [
					later(q.start) && `opens ${dateTime(q.start)}`,
					q.due && `due ${dateTime(q.due)}, ${relative(q.due)}`,
					q.end && q.end !== q.due && `closes ${dateTime(q.end)}`,
					!q.start && !q.due && !q.end && "open any time",
					q.attempts == null ? "unlimited attempts" : plural(q.attempts, "attempt"),
					q.minutes && `${q.minutes} min`,
				], past);
				const text = richField(q.description, base);
				if (text) article.append(text);
				body.append(article);
			}
			return body;
		},
		// By day, from today: the time, what it is, where, and a deadline marked.
		calendar({ items, capped, base }) {
			const body = el("div", "dtu-learn-agenda");
			body.append(el("p", "dtu-learn-muted", capped ? "The next 100 events, all DTU Learn gives at once." : "The next five months."));
			if (!items.length) body.append(el("p", undefined, "Nothing scheduled."));
			let day;
			let list;
			for (const e of items) {
				if (new Date(e.start).toDateString() !== day) {
					day = new Date(e.start).toDateString();
					list = el("ul");
					body.append(el("h3", undefined, new Date(e.start).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })), list);
				}
				const row = el("li");
				const what = el("div");
				what.append(el("span", "dtu-learn-event", e.title));
				if (e.due) what.append(el("span", "dtu-learn-tag dtu-learn-due", "deadline"));
				if (e.where) {
					// A session booked into many rooms lists them all: one line, all on hover.
					const where = el("p", "dtu-learn-where", e.where);
					where.title = e.where;
					what.append(where);
				}
				const text = richField(e.description, base);
				if (text) what.append(text);
				row.append(el("span", "dtu-learn-time", e.allDay ? "all day" : e.end !== e.start ? `${timeOf(e.start)}–${timeOf(e.end)}` : timeOf(e.start)), what);
				list.append(row);
			}
			return body;
		},
		// What the learner's LearnHarness map holds against this course: each
		// of its top-level units, with the statements studied that the course
		// covers there, each opening where; units with none are the gaps.
		coverage({ total, matched, units }, course) {
			const body = el("div", "dtu-learn-coverage");
			if (!total) {
				body.append(el("p", undefined, "Your LearnHarness map is empty, so there is nothing to hold against the course yet. It fills from your sessions when you run Extract on LearnHarness's graph page, and from documents with Extract files into the graph."));
				return body;
			}
			const byUnit = new Map();
			for (const item of matched) byUnit.set(item.unit, [...(byUnit.get(item.unit) ?? []), item]);
			const touched = units.filter(unit => byUnit.has(unit.id)).length;
			body.append(el("p", "dtu-learn-muted", `${matched.length} of the ${plural(total, "statement")} in your map are covered by this course; ${touched} of its ${plural(units.length, "unit")} have something you have studied.`));
			const chips = items => {
				const box = el("div", "dtu-learn-coverage-items");
				for (const item of items) {
					const chip = button("dtu-learn-chip", item.name, () => openRef({ ou: course.id, target: item.target, title: item.title, page: item.page }));
					chip.title = [item.statement, `${item.title}${item.page ? `, page ${item.page}` : ""}`].filter(Boolean).join("\n");
					box.append(chip);
				}
				return box;
			};
			const list = el("ul", "dtu-learn-coverage-units");
			for (const unit of units) {
				const items = byUnit.get(unit.id) ?? [];
				const row = el("li", items.length ? "studied" : "open");
				const head = el("div", "dtu-learn-coverage-head");
				head.append(button("dtu-learn-link", unit.title, () => openRef({ ou: course.id, unit: unit.id })),
					el("span", "dtu-learn-tag", items.length ? `${items.length} studied` : "nothing studied yet"));
				row.append(head);
				if (items.length) row.append(chips(items));
				list.append(row);
			}
			body.append(list);
			// Found in an announcement or a linked file, outside the units.
			const elsewhere = byUnit.get(null) ?? [];
			if (elsewhere.length) body.append(el("h3", undefined, "Elsewhere in the course"), chips(elsewhere));
			return body;
		},
		// Each group category, with the learner's group in it and its members.
		groups({ items, base }, course) {
			const body = el("div", "dtu-learn-cards");
			if (!items.length) body.append(el("p", undefined, "This course has no groups."));
			for (const g of items) {
				const signup = g.signup && (later(g.opens) ? `sign-up opens ${dateTime(g.opens)}`
					: !g.closes ? "sign-up open"
					: later(g.closes) ? `sign-up until ${dateTime(g.closes)}` : `sign-up closed ${dateTime(g.closes)}`);
				const canJoin = !g.mine && g.signup && !later(g.opens) && (!g.closes || later(g.closes));
				const article = card(g.name, canJoin && `${base}/d2l/lms/group/user_group_list.d2l?ou=${course.id}`,
					[plural(g.groups, "group"), g.size && `up to ${g.size} each`, signup]);
				const text = richField(g.description, base);
				if (text) article.append(text);
				if (!g.mine) {
					article.append(el("p", "dtu-learn-muted", "You are not in one of these groups."));
				} else {
					const line = el("p", "dtu-learn-mine", "Your group: ");
					line.append(el("strong", undefined, g.mine.name));
					article.append(line);
					const about = richField(g.mine.description, base);
					if (about) article.append(about);
					const people = el("ul", "dtu-learn-members");
					for (const m of g.mine.members) {
						const person = el("li", undefined, m.me ? `${m.name} (you)` : m.name);
						if (m.role && !m.role.startsWith("Student")) person.append(" ", el("span", "dtu-learn-tag", m.role));
						if (m.email && !m.me) person.append(" ", anchor(`mailto:${m.email}`, m.email));
						people.append(person);
					}
					// A whole exercise class is a long list: folded, its size showing.
					if (g.mine.members.length > 10) {
						const fold = el("details", "dtu-learn-members-fold");
						fold.append(el("summary", undefined, plural(g.mine.members.length, "member")), people);
						article.append(fold);
					} else {
						article.append(people);
					}
				}
				body.append(article);
			}
			return body;
		},
	};

	// Something that shows one thing at a time under a heading of its own:
	// the pane beside the tree, or a link's preview. box holds it; life ends
	// it, freeing whatever it shows.
	function panel(box, life) {
		let shown = 0;
		let release = () => {}; // frees what it shows: its buttons, a PDF
		life.addEventListener("abort", () => release(), { once: true });
		// What it shows scrolls in here, below the heading, so the scrollbar
		// starts under the heading's buttons rather than beside them.
		const scroller = el("div", "dtu-learn-scroll");

		// Replace what it shows with a heading and a body; a later call wins
		// over an earlier one still waiting. source says what it is, for a
		// quote sent to the tutor: {ou, topic, unit, attachment, linked, url}.
		let about = { title: "" };
		function heading(text, source = {}) {
			const mine = ++shown;
			about = { ...source, title: text };
			// A cleanup that throws must not keep the next item from showing.
			try { release(); } catch (error) { console.error("dtu-learn: pane cleanup failed", error); }
			release = () => {};
			const head = el("div", "dtu-learn-head");
			const title = el("h2", undefined, text);
			const slot = el("div");
			head.append(title, slot);
			const current = () => mine === shown && !life.aborted;
			const put = body => {
				if (current()) {
					scroller.replaceChildren(body);
					scroller.scrollTo(0, 0);
					box.replaceChildren(head, scroller);
				}
				return current();
			};
			return { slot, put, current, title, head };
		}
		const control = (slot, props) => {
			const holder = el("span");
			slot.append(holder);
			return ctx.ui.mountButton(holder, { variant: "secondary", size: "sm", ...props }, { signal: life });
		};
		const openButton = (slot, request, title = "Open the saved file in its usual app") =>
			control(slot, { label: "Open file", variant: "default", title, onClick: () => ctx.call("open", request) });

		// A unit, or a topic's or a file's text. failure says why a PDF is
		// shown as text.
		async function show(title, request, failure) {
			if (request.unit !== undefined) return showUnit(title, request);
			const copy = courses.get(request.ou);
			if (request.topic !== undefined && findTopic(copy?.modules, request.topic)?.TypeIdentifier === "ContentService") {
				return showVideo(title, request);
			}
			if (!failure && isPdf(request)) return showPdf(title, request);
			const { slot, put } = heading(title, request);
			put(el("p", "dtu-learn-muted", "Reading…"));
			try {
				const { text, file } = await ctx.call("text", request);
				const body = el("div");
				if (failure) body.append(el("p", "dtu-learn-error", `Could not draw the PDF (${failure.message}). Its text instead:`));
				const pre = linkify(el("pre", "dtu-learn-text"), text);
				body.append(pre);
				const marks = markWords(pre, wordsPattern(request.find));
				if (!put(body)) return;
				marks[0]?.scrollIntoView({ block: "center" });
				if (file) {
					const button = openButton(slot, request);
					release = () => button.dispose();
				}
			} catch (error) {
				put(el("p", "dtu-learn-error", error.message));
			}
		}

		// A unit's own text, with its links, and what it holds, its units and
		// topics, which open here. From the saved course, so it is the text
		// as the course page shows it, not a flattened rendition.
		async function showUnit(title, { ou, unit, find }) {
			const { put } = heading(title, { ou, unit });
			put(el("p", "dtu-learn-muted", "Reading…"));
			await load(ou);
			const copy = courses.get(ou);
			const module = copy && !(copy instanceof Error) ? findUnit(copy.modules, unit) : undefined;
			if (!module) {
				put(el("p", "dtu-learn-error", "That unit is not in the saved course. Refresh the course."));
				return;
			}
			const body = el("div");
			const { Html: html, Text: text } = module.Description ?? {};
			if (html?.trim()) body.append(rich(html, copy.base));
			else if (text?.trim()) body.append(linkify(el("div", "dtu-learn-text"), text.trim()));
			else body.append(el("p", "dtu-learn-muted", "This unit has no text of its own."));
			const items = [
				...(module.Modules ?? []).map(m => [m.Title, () => show(m.Title, { ou, unit: m.ModuleId })]),
				...(module.Topics ?? []).filter(t => !(hideBroken && t.IsBroken)).map(t => [t.Title, () => show(t.Title, { ou, topic: t.TopicId })]),
			];
			if (items.length) {
				const list = el("ul", "dtu-learn-contents");
				for (const [name, go] of items) {
					const item = el("li");
					item.append(button("dtu-learn-link", name, go));
					list.append(item);
				}
				body.append(el("h3", undefined, "Contents"), list);
			}
			const marks = markWords(body, wordsPattern(find));
			if (put(body)) marks[0]?.scrollIntoView({ block: "center" });
		}

		// A video plays here, from Brightspace's media service through a
		// short-lived link. Download keeps a copy in the course folder, only
		// when asked, and then Open file plays it in the system's player,
		// offline too.
		async function showVideo(title, request) {
			const { slot, put, current } = heading(title, request);
			put(el("p", "dtu-learn-muted", "Getting the video…"));
			const handles = [];
			release = () => handles.forEach(handle => handle.dispose());
			const note = el("p", "dtu-learn-muted");
			const bar = el("progress", "dtu-learn-progress");
			bar.hidden = true;
			const mb = bytes => `${Math.round(bytes / 1e6)} MB`;
			let size; // the video's, when the media service says
			// The one button: Download, or Open file once a copy is saved.
			const offer = saved => {
				handles.splice(0).forEach(handle => handle.dispose());
				if (saved) {
					handles.push(openButton(slot, request, "Play the saved copy in your video player, offline too"));
					return;
				}
				const download = control(slot, {
					label: "Download",
					title: `Save this video${size ? `, ${mb(size)},` : ""} into the course folder, to play it offline with Open file`,
					onClick: async () => {
					download.update({ label: "Downloading…", disabled: true, title: "Saving the video into the course folder" });
					note.textContent = "Downloading the video into the course folder…";
					bar.hidden = false;
					bar.removeAttribute("value"); // moving, until the first progress
					// The backend reports each percent as a notification.
					const follow = data => {
						if (data?.ou !== request.ou || data?.topic !== request.topic || !current()) return;
						bar.max = data.total;
						bar.value = data.done;
						download.update({ label: `Downloading ${Math.floor(data.done * 100 / data.total)}%` });
						note.textContent = `Downloading into the course folder: ${mb(data.done)} of ${mb(data.total)}.`;
					};
					videoProgress.add(follow);
					try {
						const { summary } = await ctx.call("download-video", request);
						// The course's record now has it: read it again, for the tree too.
						courses.delete(request.ou);
						loads.delete(request.ou);
						await load(request.ou);
						if (!current()) return;
						note.textContent = summary;
						bar.hidden = true;
						offer(true);
					} catch (error) {
						if (!current()) return;
						note.textContent = error.message;
						bar.hidden = true;
						download.update({ label: "Download", disabled: false, title: "Try saving the video again" });
					} finally {
						videoProgress.delete(follow);
					}
				} });
				handles.push(download);
			};
			const answer = await ctx.call("video", request);
			const { url, saved, error } = answer;
			size = answer.size;
			if (!current()) return;
			offer(saved);
			if (error) {
				note.textContent = saved
					? `The video cannot be streamed now (${error}). Its saved copy opens with Open file.`
					: error;
				const body = el("div");
				body.append(bar, note);
				put(body);
				return;
			}
			const video = el("video", "dtu-learn-video");
			video.controls = true;
			video.preload = "metadata";
			video.src = url;
			video.addEventListener("error", () => {
				note.textContent = "LearnHarness would not play it from the media service. Download it and use Open file, or watch it on DTU Learn in your browser.";
			});
			note.textContent = saved
				? "Playing from Brightspace's media service. A copy is saved: Open file plays it offline."
				: "Playing from Brightspace's media service. Download keeps a copy for offline use.";

			// Full screen. LearnHarness does not let a page element take the
			// screen: the request goes unanswered or is undone at once, so the
			// video's own button does nothing and is hidden. This one has the
			// video fill the window instead, asking for nothing: the player
			// moves to the top of the page, over the app, until Escape, a
			// double click or Exit full screen. Moved within one task, a
			// playing video keeps playing.
			const page = container.ownerDocument;
			const player = el("div", "dtu-learn-player");
			const home = el("div"); // where the player sits when not filling the window
			const exit = button("dtu-learn-exit", "Exit full screen", () => fill(false));
			player.append(video, exit);
			home.append(player);
			const keys = new AbortController();
			let filling = false;
			const fill = on => {
				if (on === filling) return;
				filling = on;
				player.classList.toggle("dtu-learn-fill", on);
				(on ? page.body : home).append(player);
				full.update({ label: on ? "Exit full screen" : "Full screen" });
			};
			const toggle = () => fill(!filling);
			video.setAttribute("controlslist", "nofullscreen");
			video.addEventListener("dblclick", toggle);
			win.addEventListener("keydown", event => {
				if (!filling || event.key !== "Escape") return;
				event.preventDefault();
				event.stopImmediatePropagation(); // this Escape is the video's, not the app's
				fill(false);
			}, { capture: true, signal: keys.signal });
			// Not one of handles: those make way for Open file after a download.
			const full = control(slot, {
				label: "Full screen", onClick: toggle,
				title: "Fill the window with the video. Escape, a double click or Exit full screen brings it back.",
			});
			const close = release;
			release = () => {
				close();
				full.dispose();
				keys.abort();
				player.remove(); // from the top of the page too, if filling
			};
			const body = el("div");
			body.append(home, bar, note);
			put(body);
		}

		// A course's page: tabs over its announcements, as its copy saved
		// them, and its assignments, calendar, groups and quizzes, read from
		// DTU Learn when their tab is first opened. It opens on the
		// announcements; focus is one of them to scroll to and outline, a
		// search result's, with query's words marked in it.
		function showCourse(course, focus, query) {
			const { put, head } = heading(course.name, { ou: course.id });
			const tabs = el("div", "dtu-learn-tabs");
			tabs.setAttribute("role", "tablist");
			tabs.setAttribute("aria-label", "Course page");
			head.append(tabs);
			let showing;
			const select = name => {
				showing = name;
				for (const tab of tabs.children) {
					const on = tab.dataset.tab === name;
					tab.setAttribute("aria-selected", String(on));
					tab.tabIndex = on ? 0 : -1;
				}
				if (name === "news") showNews();
				else showLive(name);
			};
			for (const [name, label] of Object.entries(COURSE_TABS)) {
				const tab = button("dtu-learn-tab", label, () => select(name));
				tab.setAttribute("role", "tab");
				tab.dataset.tab = name;
				tabs.append(tab);
			}
			// The arrow keys move along the tabs, as in any tab list.
			tabs.addEventListener("keydown", event => {
				const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
				if (!step) return;
				event.preventDefault();
				const all = [...tabs.children];
				const next = all[(all.findIndex(tab => tab.dataset.tab === showing) + step + all.length) % all.length];
				select(next.dataset.tab);
				next.focus();
			});

			// Its announcements, newest first. Their attachments open here like topics.
			async function showNews() {
				if (!course.saved) {
					put(el("p", "dtu-learn-muted", "Not downloaded yet. Download it to browse its units and read its announcements here. The other tabs are read from DTU Learn."));
					return;
				}
				put(el("p", "dtu-learn-muted", "Reading…"));
				await load(course.id);
				if (showing !== "news") return;
				const copy = courses.get(course.id);
				if (!copy || copy instanceof Error) {
					put(el("p", "dtu-learn-error", copy?.message ?? "Could not read the course."));
					return;
				}
				const body = el("div", "dtu-learn-cards dtu-learn-news");
				body.append(el("p", "dtu-learn-muted", `Announcements as saved ${copy.saved}. Refresh the course for newer ones.`));
				if (!copy.news.length) body.append(el("p", undefined, "No announcements."));
				let found;
				for (const news of copy.news) {
					const item = el("article");
					item.dataset.news = news.Id;
					if (news.Id === focus) found = item;
					item.append(
						el("h3", undefined, news.Title),
						el("p", "dtu-learn-muted", (news.StartDate ?? "").slice(0, 10)),
						// Its HTML, for its links; else the plain rendition, which keeps
						// every empty paragraph, so one gap will do.
						news.Body?.Html?.trim() ? rich(news.Body.Html, copy.base)
							: linkify(el("div", "dtu-learn-text"), (news.Body?.Text ?? "").trim().replace(/(\s*\n){3,}/g, "\n\n")),
					);
					for (const file of news.Attachments ?? []) {
						const line = el("p", "dtu-learn-attachment", "Attachment: ");
						line.append(button("dtu-learn-link", file.FileName,
							() => show(file.FileName, { ou: course.id, attachment: `${news.Id}/${file.FileId}` })));
						item.append(line);
					}
					body.append(item);
				}
				if (put(body) && found) {
					markWords(found, wordsPattern(query));
					found.classList.add("dtu-learn-found");
					found.scrollIntoView({ block: "start" });
				}
				focus = undefined; // once: coming back to the tab starts at the top
			}

			// A live tab, with when it was read and a way to read it again.
			async function showLive(name) {
				put(el("p", "dtu-learn-muted", "Reading from DTU Learn…"));
				let data;
				try {
					data = await tabData(course.id, name);
				} catch (error) {
					if (showing === name) put(el("p", "dtu-learn-error", error.message));
					return;
				}
				if (showing !== name) return;
				const body = el("div");
				const note = el("p", "dtu-learn-muted dtu-learn-live", name === "coverage"
					? `Your LearnHarness map against the saved course, read at ${timeOf(data.read)}. `
					: `Live from DTU Learn, read at ${timeOf(data.read)}. `);
				note.append(button("dtu-learn-link", "Read again", () => {
					tabCache.delete(`${course.id}:${name}`);
					showLive(name);
				}));
				body.append(note, LIVE_TABS[name](data, course));
				put(body);
			}

			select("news");
		}

		// A link's page. A DTU Learn link to a topic or a unit of a saved
		// course shows that, as the tree would. Where a site lets other apps
		// frame it, the page itself, with its text a click away; otherwise
		// its text.
		async function showWeb(url) {
			const { slot, put, current, title } = heading(url, { url });
			const copy = control(slot, { label: "Copy link", onClick: async () => {
				await navigator.clipboard.writeText(url);
				copy.update({ label: "Copied" });
			} });
			const browse = control(slot, { label: "Open in browser", onClick: () => openUrl(url) });
			const handles = [copy, browse];
			release = () => handles.forEach(handle => handle.dispose());
			put(el("p", "dtu-learn-muted", "Loading…"));
			try {
				const result = await ctx.call("preview", { url });
				if (!current()) return;
				if (result.target) {
					await load(result.target.ou);
					if (current()) show(result.title, result.target);
					return;
				}
				if (result.browser) {
					put(el("p", undefined, result.browser));
					return;
				}
				title.textContent = result.title;
				const body = el("div", "dtu-learn-web");
				const text = linkify(el("div", "dtu-learn-text"), result.text);
				body.append(el("p", "dtu-learn-muted dtu-learn-url", result.url));
				if (!result.frame) {
					body.append(el("p", "dtu-learn-muted", "This site does not let other apps show it, so here is its text."), text);
					put(body);
					return;
				}
				// Sandboxed: the page keeps its own site's origin, so its scripts
				// work, but it cannot open windows or steer the app.
				const frame = el("iframe", "dtu-learn-frame");
				frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
				frame.setAttribute("referrerpolicy", "no-referrer");
				frame.title = result.title;
				frame.src = result.url;
				text.hidden = true;
				body.classList.add("framed");
				body.append(frame, text);
				if (!put(body)) return;
				// Both stay mounted and only one shows: moving a frame reloads it.
				const flip = control(slot, { label: "Show text", onClick: () => {
					frame.hidden = !frame.hidden;
					text.hidden = !text.hidden;
					body.classList.toggle("framed", !frame.hidden);
					flip.update({ label: frame.hidden ? "Show page" : "Show text" });
				} });
				handles.push(flip);
			} catch (error) {
				put(el("p", "dtu-learn-error", error.message));
			}
		}

		// A PDF's pages as they look, with their text selectable for quoting and
		// a zoom. Each page is drawn when it comes near the screen and cleared
		// when it goes far off, so a long deck holds only a few pages' pixels.
		async function showPdf(title, request) {
			const { slot, put, current, head } = heading(title, request);
			put(el("p", "dtu-learn-muted", "Opening…"));
			// pdf.js 6 frees a document, and its worker, through the loading task;
			// the document itself has no destroy().
			let loading;
			try {
				const [lib, { data }] = await Promise.all([pdfjs(), ctx.call("pdf", request)]);
				loading = lib.getDocument({ data: Uint8Array.from(atob(data), c => c.charCodeAt(0)) });
				const doc = await loading.promise;
				const first = (await doc.getPage(1)).getViewport({ scale: 1 });
				const pages = el("div", "dtu-learn-pages");
				pages.style.width = `${zoom * 100}%`;
				if (!put(pages)) return loading.destroy();
				const ratio = container.ownerDocument.defaultView.devicePixelRatio;

				// Find in the PDF, on a row under its title. Matches are counted
				// from every page's text, read on the first search; a page marks
				// its own when it is drawn. Both go a text item at a time, so the
				// count and the marks agree. A search result arrives with its words.
				const findRow = el("div", "dtu-learn-find");
				const field = el("input");
				field.type = "search";
				field.placeholder = "Find in this PDF (Ctrl+F)";
				field.setAttribute("aria-label", "Find in this PDF");
				const count = el("small", "dtu-learn-muted");
				findRow.append(field, count);
				head.append(findRow);
				let pattern; // the words being found
				let matches = []; // { page, nth }: the nth mark on a page, in reading order
				let at = -1; // which match is the current one
				let follow = false; // scroll to the current match once its page has it
				let pageTexts; // every page's text items
				let finding = 0;
				// The current match, marked apart, and scrolled to if it was asked for.
				const showCurrent = box => {
					const match = matches[at];
					const mark = match && pages.children[match.page - 1] === box ? box.marks?.[match.nth] : undefined;
					if (!mark) return;
					pages.querySelector(".dtu-learn-current-match")?.classList.remove("dtu-learn-current-match");
					mark.classList.add("dtu-learn-current-match");
					if (follow) mark.scrollIntoView({ block: "center", inline: "nearest" });
					follow = false;
				};
				// Its page is drawn, and marked, when it comes near the screen.
				const goTo = index => {
					if (!matches.length) return;
					at = (index + matches.length) % matches.length;
					count.textContent = `${at + 1} of ${matches.length}`;
					const box = pages.children[matches[at].page - 1];
					follow = true;
					if (box.marks) showCurrent(box);
					else box.scrollIntoView({ block: "start" });
				};
				// The first page at least partly in view.
				const topPage = () => {
					const top = scroller.getBoundingClientRect().top;
					return 1 + Math.max(0, [...pages.children].findIndex(box => box.getBoundingClientRect().bottom > top));
				};
				// The first match is the first from fromPage on, else from the page in view.
				const findText = async (value, fromPage) => {
					const mine = ++finding;
					pattern = wordsPattern(value);
					matches = [];
					at = -1;
					follow = false;
					for (const box of pages.children) {
						const layer = box.marks && box.querySelector(".textLayer");
						if (!layer) continue;
						unmark(layer);
						box.marks = markWords(layer, pattern);
					}
					count.textContent = pattern ? "Searching…" : "";
					if (!pattern) return;
					pageTexts ??= Promise.all(Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1)
						.then(page => page.getTextContent())
						.then(content => content.items.map(item => item.str ?? ""))));
					let texts;
					try {
						texts = await pageTexts;
					} catch (error) {
						pageTexts = undefined;
						if (mine === finding && current()) count.textContent = "Could not read its text";
						return;
					}
					if (mine !== finding || !current()) return;
					texts.forEach((items, i) => {
						let nth = 0;
						for (const str of items) for (const _ of str.matchAll(pattern)) matches.push({ page: i + 1, nth: nth++ });
					});
					if (!matches.length) {
						count.textContent = "No matches";
						return;
					}
					const start = fromPage ?? topPage();
					goTo(Math.max(0, matches.findIndex(match => match.page >= start)));
				};

				// A page is a box holding its picture and, over it, pdf.js's text
				// layer: the page's text, transparent, where the words are, so a
				// selection covers what it looks like it covers. Both are made off
				// screen and swapped in together, so a page being redrawn at a new
				// zoom keeps its old picture, stretched, until the new one is ready.
				const draw = async box => {
					if (box.wanted) return;
					box.wanted = true;
					const page = await doc.getPage(Number(box.dataset.page));
					if (!box.wanted) return;
					const base = page.getViewport({ scale: 1 });
					const scale = Math.max(1, box.clientWidth) / base.width;
					const viewport = page.getViewport({ scale: scale * ratio });
					const canvas = el("canvas");
					canvas.width = Math.floor(viewport.width);
					canvas.height = Math.floor(viewport.height);
					const text = el("div", "textLayer");
					const task = page.render({ canvas, viewport });
					box.task = task;
					box.text = new lib.TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport: page.getViewport({ scale }) });
					const [, , annotations] = await Promise.all([task.promise, box.text.render(), page.getAnnotations()]);
					if (box.task !== task) return; // redrawn or forgotten meanwhile
					// pdf.js's viewer adds this so a drag past the last word does not
					// select the whole page.
					text.append(el("div", "endOfContent"));
					text.addEventListener("pointerdown", () => text.classList.add("selecting"));
					box.style.aspectRatio = `${base.width} / ${base.height}`;
					box.style.setProperty("--total-scale-factor", scale);
					box.drawnWidth = box.clientWidth;
					box.marks = markWords(text, pattern);
					box.replaceChildren(canvas, text, links(page, annotations));
					showCurrent(box);
				};
				// The PDF's own links, over the words they sit on: to a web page,
				// opened in the browser, or to another page of the PDF, scrolled
				// to. Placed in shares of the page, so they stay put at any zoom.
				const links = (page, annotations) => {
					const layer = el("div", "dtu-learn-links");
					const base = page.getViewport({ scale: 1 });
					for (const note of annotations) {
						if (note.subtype !== "Link" || !(note.url || note.dest)) continue;
						// pdf.js 6 converts points only, so the rectangle goes corner by corner.
						const [x1, y1] = base.convertToViewportPoint(note.rect[0], note.rect[1]);
						const [x2, y2] = base.convertToViewportPoint(note.rect[2], note.rect[3]);
						const link = note.url ? anchor(note.url, "") : el("a");
						if (note.url) link.title = note.url;
						else link.addEventListener("click", () => jump(note.dest).catch(() => {}));
						Object.assign(link.style, {
							left: `${Math.min(x1, x2) / base.width * 100}%`, top: `${Math.min(y1, y2) / base.height * 100}%`,
							width: `${Math.abs(x2 - x1) / base.width * 100}%`, height: `${Math.abs(y2 - y1) / base.height * 100}%`,
						});
						layer.append(link);
					}
					return layer;
				};
				// A destination is a name to look up or [page, how to fit it];
				// the page is a reference, or its index in some PDFs.
				const jump = async dest => {
					const target = typeof dest === "string" ? await doc.getDestination(dest) : dest;
					if (!target) return;
					const index = typeof target[0] === "number" ? target[0] : await doc.getPageIndex(target[0]);
					pages.children[index]?.scrollIntoView({ block: "start" });
				};
				const stop = box => {
					box.wanted = false;
					box.task?.cancel();
					box.text?.cancel();
					box.task = box.text = undefined;
				};
				const forget = box => {
					if (!box.wanted) return;
					stop(box);
					box.marks = undefined;
					box.replaceChildren(); // frees the pixels; CSS keeps the page's place
				};
				// Drawing is cancelled whenever a page scrolls far off or the PDF
				// is closed; anything else is a fault worth seeing in the console.
				const failed = error => {
					if (!/Cancel|Abort/.test(error?.name)) console.error("dtu-learn: could not draw a page", error);
				};
				const observer = new IntersectionObserver(entries => {
					for (const { target, isIntersecting } of entries) {
						if (isIntersecting) draw(target).catch(failed);
						else forget(target);
					}
				}, { rootMargin: "100% 0px" });
				for (let n = 1; n <= doc.numPages; n++) {
					const box = el("div", "dtu-learn-page");
					box.dataset.page = n;
					box.setAttribute("aria-label", `Page ${n} of ${doc.numPages}`);
					box.style.aspectRatio = `${first.width} / ${first.height}`;
					pages.append(box);
					observer.observe(box);
				}
				// A search result names the page its words are on: start there,
				// and find them, from there on.
				if (request.page > 1) pages.children[request.page - 1]?.scrollIntoView({ block: "start" });
				if (request.find) {
					field.value = request.find;
					findText(request.find, request.page ?? 1);
				}

				// Zoom: pages are a share of the pane's width, which the buttons
				// and Ctrl+wheel (also a touchpad pinch) change. The new size shows
				// at once, stretched; once the width stops changing, by zoom, by
				// the bar between the columns or by the window, the pages in view
				// are drawn sharp at their new width.
				const listening = new AbortController();
				let settle;
				const zoomTo = value => {
					zoom = Math.min(4, Math.max(0.5, value));
					const at = scroller.scrollTop / Math.max(1, scroller.scrollHeight);
					pages.style.width = `${zoom * 100}%`;
					scroller.scrollTop = at * scroller.scrollHeight;
					reset.update({ label: `${Math.round(zoom * 100)}%` });
				};
				const resized = new ResizeObserver(() => {
					clearTimeout(settle);
					settle = setTimeout(() => {
						for (const box of pages.children) {
							if (box.wanted && Math.abs(box.clientWidth - box.drawnWidth) > 1) { stop(box); draw(box).catch(failed); }
						}
					}, 200);
				});
				resized.observe(pages);
				const out = control(slot, { label: "Zoom out", icon: MINUS, iconOnly: true, size: "icon-sm", onClick: () => zoomTo(zoom / 1.25) });
				const reset = control(slot, { label: `${Math.round(zoom * 100)}%`, title: "Fit to width", onClick: () => zoomTo(1) });
				const into = control(slot, { label: "Zoom in", icon: PLUS, iconOnly: true, size: "icon-sm", onClick: () => zoomTo(zoom * 1.25) });
				scroller.addEventListener("wheel", event => {
					if (!event.ctrlKey) return;
					event.preventDefault();
					zoomTo(zoom * Math.exp(-event.deltaY / 300));
				}, { passive: false, signal: listening.signal });
				container.ownerDocument.addEventListener("pointerup", () => {
					for (const layer of pages.querySelectorAll(".textLayer.selecting")) layer.classList.remove("selecting");
				}, { signal: listening.signal });

				// Typing finds a moment after the last key; Enter goes to the next
				// match, Shift+Enter to the one before; Escape empties the box,
				// then leaves it. Ctrl+F (Cmd+F) comes to it from the PDF.
				let typing;
				field.addEventListener("input", () => {
					clearTimeout(typing);
					typing = setTimeout(() => findText(field.value), 150);
				}, { signal: listening.signal });
				field.addEventListener("keydown", event => {
					if (event.key === "Enter") {
						event.preventDefault();
						clearTimeout(typing);
						if (wordsPattern(field.value)?.source !== pattern?.source) findText(field.value);
						else goTo(at + (event.shiftKey ? -1 : 1));
					} else if (event.key === "Escape") {
						event.preventDefault();
						event.stopPropagation(); // the box's Escape, not the view's or the app's
						if (!field.value) return field.blur();
						field.value = "";
						clearTimeout(typing);
						findText("");
					}
				}, { signal: listening.signal });
				const previous = control(findRow, { label: "Previous match", icon: UP, iconOnly: true, size: "icon-sm", title: "Previous match (Shift+Enter)", onClick: () => goTo(at - 1) });
				const next = control(findRow, { label: "Next match", icon: DOWN, iconOnly: true, size: "icon-sm", title: "Next match (Enter)", onClick: () => goTo(at + 1) });
				findRow.append(count); // after the arrows
				win.addEventListener("keydown", event => {
					if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "f") return;
					const target = event.target;
					if (!box.contains(target) && !(box === pane && target === container.ownerDocument.body)) return;
					event.preventDefault();
					field.focus();
					field.select();
				}, { capture: true, signal: listening.signal });

				// Send page: the page taking most of the pane, drawn as get_pdf_pages
				// draws pages for the tutors (1568 pixels on its long side), queued
				// for the learner's next message.
				const pageInView = () => {
					const view = scroller.getBoundingClientRect();
					let best = 1;
					let most = -Infinity;
					for (const box of pages.children) {
						const rect = box.getBoundingClientRect();
						const visible = Math.min(rect.bottom, view.bottom) - Math.max(rect.top, view.top);
						if (visible > most) [most, best] = [visible, Number(box.dataset.page)];
					}
					return best;
				};
				const pagePng = async n => {
					const page = await doc.getPage(n);
					const base = page.getViewport({ scale: 1 });
					const viewport = page.getViewport({ scale: 1568 / Math.max(base.width, base.height) });
					const canvas = el("canvas");
					canvas.width = Math.round(viewport.width);
					canvas.height = Math.round(viewport.height);
					await page.render({ canvas, viewport }).promise;
					return canvas.toDataURL("image/png").slice("data:image/png;base64,".length);
				};
				let sentLabel;
				const send = control(slot, {
					label: "Send page",
					title: "Send the page in view to your tutor, as an image, with your next message in a teaching or practice session",
					onClick: async () => {
						const n = pageInView();
						const image = await pagePng(n);
						if (!current()) return;
						tutor.add({ ...materialSource(about), page: n, image });
						send.update({ label: `Page ${n} queued` });
						clearTimeout(sentLabel);
						sentLabel = setTimeout(() => current() && send.update({ label: "Send page" }), 1600);
					},
				});
				const button = openButton(slot, request);
				const task = loading;
				release = () => {
					listening.abort();
					clearTimeout(settle);
					clearTimeout(typing);
					clearTimeout(sentLabel);
					resized.disconnect();
					observer.disconnect();
					for (const handle of [out, reset, into, send, button, previous, next]) handle.dispose();
					task.destroy();
				};
			} catch (error) {
				loading?.destroy();
				if (current()) show(title, request, error);
			}
		}

		return { heading, show, showCourse, showWeb, about: () => about };
	}
	const main = panel(pane, signal);

	// Where queued material is from, as the tutor's message will say it, and
	// where it sits, to go back to from the queue: what the pane showed, a
	// file, a unit or a course page (with the announcement, news).
	function materialSource(about, news) {
		const { title, ou, topic } = about;
		const target = Object.fromEntries(["topic", "unit", "attachment", "linked"]
			.filter(key => about[key] !== undefined).map(key => [key, about[key]]));
		if (!Object.keys(target).length) target.news = news ?? null;
		return {
			title, ou, topic, course: terms.flatMap(term => term.courses).find(course => course.id === ou)?.name,
			place: ou && !about.url ? { title, target } : undefined,
		};
	}

	// Send to tutor: selected text in the pane, a PDF's or any other, gets a
	// button beside it that queues it as a quote for the learner's next
	// message, with the page it is on.
	const doc = container.ownerDocument;
	const quote = button("dtu-learn-action dtu-learn-quote", "Send to tutor", () => {
		const selection = doc.getSelection();
		const text = selection.toString().trim();
		if (!text) return;
		const node = selection.anchorNode?.nodeType === 1 ? selection.anchorNode : selection.anchorNode?.parentElement;
		const page = Number(node?.closest(".dtu-learn-page")?.dataset.page) || undefined;
		// On a course page, the announcement, assignment or quiz it is from.
		const article = node?.closest("article");
		const card = article?.querySelector("h3")?.textContent;
		const source = materialSource(main.about(), Number(article?.dataset.news) || undefined);
		tutor.add({ ...source, title: card ?? source.title, page, text: text.slice(0, 6000) });
		selection.removeAllRanges();
	});
	quote.title = "Quote this with your next message to your tutor, in a teaching or practice session";
	quote.hidden = true;
	// Pressing it must not take the selection away before the click.
	quote.addEventListener("pointerdown", event => event.preventDefault());
	root.append(quote);
	const placeQuote = () => {
		const selection = doc.getSelection();
		const range = selection?.rangeCount && !selection.isCollapsed ? selection.getRangeAt(0) : undefined;
		const show = !!range && pane.contains(range.commonAncestorContainer) && !!selection.toString().trim();
		quote.hidden = !show;
		if (!show) return;
		// Just under the selection's last line, kept inside the view.
		const rects = range.getClientRects();
		const end = rects[rects.length - 1] ?? range.getBoundingClientRect();
		const box = root.getBoundingClientRect();
		const view = (pane.querySelector(".dtu-learn-scroll") ?? pane).getBoundingClientRect(); // below the heading
		const top = Math.min(Math.max(end.bottom, view.top), view.bottom - quote.offsetHeight - 4);
		quote.style.left = `${Math.max(view.left - box.left, Math.min(end.right - box.left - quote.offsetWidth / 2, view.right - box.left - quote.offsetWidth - 4))}px`;
		quote.style.top = `${top - box.top + 6}px`;
	};
	doc.addEventListener("selectionchange", placeQuote, { signal });
	pane.addEventListener("scroll", placeQuote, { capture: true, signal });

	// What waits for the tutor, under the tree: each item, removable, until
	// the next message takes it.
	const outbox = el("div", "dtu-learn-outbox");
	left.append(outbox);
	const drawOutbox = items => {
		outbox.hidden = !items.length;
		if (!items.length) return outbox.replaceChildren();
		const top = el("div", "dtu-learn-outbox-head");
		top.append(el("strong", undefined, `For your tutor (${items.length})`),
			button("dtu-learn-link", "Clear", () => tutor.remove(tutor.items)));
		const list = el("ul");
		for (const item of items) {
			const where = `${item.title}${item.page ? `, p. ${item.page}` : ""}`;
			const entry = el("li");
			const label = item.image ? `Page ${item.page} of ${item.title}` : `“${item.text.replace(/\s+/g, " ").slice(0, 70)}${item.text.length > 70 ? "…" : ""}” ${where}`;
			// Click it to go back to where it is from, at its page.
			const place = item.place;
			if (place) {
				const back = button("dtu-learn-link", label, () => go({ ou: item.ou, course: item.course, title: place.title, target: place.target, page: item.page }));
				back.title = `Go back to ${where}`;
				entry.append(back);
			} else {
				entry.append(el("span", undefined, label));
			}
			const drop = button("dtu-learn-link dtu-learn-remove", "×", () => tutor.remove([item]));
			drop.setAttribute("aria-label", "Remove");
			drop.title = "Remove";
			entry.append(drop);
			list.append(entry);
		}
		outbox.replaceChildren(top, list, el("p", "dtu-learn-muted", "Sent with your next message in a teaching or practice session."));
	};
	drawOutbox(tutor.items);
	tutor.listen(drawOutbox, signal);

	async function download(course, action) {
		const { put } = main.heading(course.name);
		action.disabled = true;
		action.textContent = "Downloading…";
		put(el("p", "dtu-learn-muted", "Downloading the whole course. This takes a minute or two."));
		try {
			const { summary, saved } = await ctx.call("download", { ou: course.id });
			course.saved = saved;
			// The tree may have changed; build its rows afresh.
			for (const key of cache.keys()) if (/^[up]:(\d+):/.exec(key)?.[1] === String(course.id)) cache.delete(key);
			courses.delete(course.id);
			loads.delete(course.id);
			if (open.has(`c:${course.id}`)) load(course.id);
			put(el("pre", "dtu-learn-text", summary));
		} catch (error) {
			put(el("p", "dtu-learn-error", error.message));
		} finally {
			action.disabled = false;
			action.textContent = course.saved ? "Refresh" : "Download";
			if (!signal.aborted) redraw();
		}
	}

	(async () => {
		let text = "";
		try {
			({ note: text, terms } = await ctx.call("courses"));
		} catch (error) {
			text = error.message;
		}
		if (signal.aborted) return;
		note.textContent = text;
		note.hidden = !text;
		if (terms[0]) open.add(`t:${terms[0].name}`);
		widget = ctx.ui.mountTree(treeBox, {
			label: "DTU Learn courses", header: "Course",
			columns: [{ key: "info", header: "Info", width: 96, align: "right" }],
			rows: rows(),
		}, { signal });
		// Opened at a reference from a tutor's answer: show it.
		if (opener.target) openRef(opener.target);
	})();

	// What a reference, a tool card or a search hit names, in the pane and
	// the tree, at its page: a topic, a unit, a hit's own target (with its
	// title), or with none of them the course's page.
	async function openRef({ ou, topic, unit, page, target, title }) {
		await load(ou);
		const copy = courses.get(ou);
		const name = terms.flatMap(term => term.courses).find(course => course.id === ou)?.name;
		target ??= topic !== undefined ? { topic } : unit !== undefined ? { unit } : { news: null };
		const found = copy && !(copy instanceof Error) && (
			target.topic !== undefined ? findTopic(copy.modules, target.topic)
			: target.unit !== undefined ? findUnit(copy.modules, target.unit)
			: { Title: title ?? name });
		if (!found) {
			main.heading("Not found").put(el("p", "dtu-learn-error", "That is not in your saved copy of the course. Refresh the course, then try again."));
			return;
		}
		go({ ou, course: name, title: title ?? found.Title, target, page });
	}

	return () => {
		widget?.dispose(); // the pane's panel frees what it shows when signal aborts
		root.remove();
	};
}
