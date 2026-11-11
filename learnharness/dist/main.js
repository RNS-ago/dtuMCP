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

const MINUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg>';
const PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';

export function activate(ctx) {
	ctx.registerView({ id: "courses", name: "DTU Learn", mount: (container, signal) => mount(ctx, container, signal) });
	ctx.registerAction({ id: "open", name: "DTU Learn", icon: ICON, run: () => ctx.app.openView("courses") });
	ctx.placeAction({ id: "sidebar-open", actionId: "open", location: "sidebar" });
}

function mount(ctx, container, signal) {
	const el = (tag, className, text) => {
		const node = container.ownerDocument.createElement(tag);
		if (className) node.className = className;
		if (text !== undefined) node.textContent = text;
		return node;
	};
	const root = el("div", "dtu-learn");
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
	});
	toggle.append(box, "Hide broken topics");
	left.append(note, toggle, treeBox);
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
		main.showCourse(course);
	}

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
					main.show(m.Title, { ou, unit: m.ModuleId });
				})),
				...(children.length && { disclosure: fold(key, m.Title), children }),
			};
		};
		const topic = t => {
			const key = `p:${ou}:${t.TopicId}`;
			return {
				key, content: once(key, () => button("dtu-learn-link", t.Title, () => main.show(t.Title, { ou, topic: t.TopicId }))),
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
		// over an earlier one still waiting.
		function heading(text) {
			const mine = ++shown;
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
			return { slot, put, current, title };
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
			const { slot, put } = heading(title);
			put(el("p", "dtu-learn-muted", "Reading…"));
			try {
				const { text, file } = await ctx.call("text", request);
				const body = el("div");
				if (failure) body.append(el("p", "dtu-learn-error", `Could not draw the PDF (${failure.message}). Its text instead:`));
				body.append(linkify(el("pre", "dtu-learn-text"), text));
				if (put(body) && file) {
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
		async function showUnit(title, { ou, unit }) {
			const { put } = heading(title);
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
			put(body);
		}

		// A video plays here, from Brightspace's media service through a
		// short-lived link. Download keeps a copy in the course folder, only
		// when asked, and then Open file plays it in the system's player,
		// offline too.
		async function showVideo(title, request) {
			const { slot, put, current } = heading(title);
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

		// A course's announcements, newest first, as its copy saved them.
		// Their attachments open here like topics.
		async function showCourse(course) {
			const { put } = heading(course.name);
			if (!course.saved) {
				put(el("p", "dtu-learn-muted", "Not downloaded yet. Download it to browse its units and read its announcements here."));
				return;
			}
			put(el("p", "dtu-learn-muted", "Reading…"));
			await load(course.id);
			const copy = courses.get(course.id);
			if (!copy || copy instanceof Error) {
				put(el("p", "dtu-learn-error", copy?.message ?? "Could not read the course."));
				return;
			}
			const body = el("div", "dtu-learn-news");
			body.append(el("p", "dtu-learn-muted", `Announcements as saved ${copy.saved}. Refresh the course for newer ones.`));
			if (!copy.news.length) body.append(el("p", undefined, "No announcements."));
			for (const news of copy.news) {
				const item = el("article");
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
			put(body);
		}

		// A link's page. A DTU Learn link to a topic or a unit of a saved
		// course shows that, as the tree would. Where a site lets other apps
		// frame it, the page itself, with its text a click away; otherwise
		// its text.
		async function showWeb(url) {
			const { slot, put, current, title } = heading(url);
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
			const { slot, put, current } = heading(title);
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
					box.replaceChildren(canvas, text, links(page, annotations));
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

				const button = openButton(slot, request);
				const task = loading;
				release = () => {
					listening.abort();
					clearTimeout(settle);
					resized.disconnect();
					observer.disconnect();
					for (const handle of [out, reset, into, button]) handle.dispose();
					task.destroy();
				};
			} catch (error) {
				loading?.destroy();
				if (current()) show(title, request, error);
			}
		}

		return { heading, show, showCourse, showWeb };
	}
	const main = panel(pane, signal);

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
	})();

	return () => {
		widget?.dispose(); // the pane's panel frees what it shows when signal aborts
		root.remove();
	};
}
