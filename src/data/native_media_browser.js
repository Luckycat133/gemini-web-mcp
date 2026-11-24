async ({action, kind, model, model_token, prompt, url, offset, size, option, duration_seconds, aspect_ratio, thinking_level,
        music_duration, music_vocals, music_tokens, reference_required, reference_token}) => {
    const gemini = "https://gemini.google.com";
    if (location.origin !== gemini || window !== window.top) return null;
    const atComposer = location.pathname === "/app" || location.pathname === "/app/";
    const responseSelector = 'model-response, [id^="model-response-message-content"], [data-model-response-turn-id]';
    const userQuerySelector = 'user-query, [data-test-id="user-query"]';
    const responseRegionSelector = `${responseSelector}, [data-test-id="model-response"]`;
    const musicOutputSelector = '[data-test-id="music-generation-output"]';
    const buttonSelector = 'button, [role="button"], [role="menuitem"], [role="menuitemcheckbox"]';
    const visible = el => el.getClientRects().length > 0 && !el.closest('[hidden], [inert], [aria-hidden="true"]')
        && getComputedStyle(el).visibility === "visible" && getComputedStyle(el).opacity !== "0";
    const usable = el => visible(el) && !el.matches(':disabled, [aria-disabled="true"]');
    const norm = text => (text || "").replace(/\s+/g, " ").trim();
    const name = el => norm(el.getAttribute("aria-label") || el.innerText);
    const matches = (selector, predicate) => Array.from(document.querySelectorAll(selector)).filter(el => usable(el) && predicate(el));
    const named = labels => matches(buttonSelector, el => labels.includes(name(el)));
    const coordinate = nodes => {
        if (nodes.length !== 1) return null;
        const target = nodes[0];
        target.scrollIntoView({block: "nearest", inline: "nearest"});
        const rect = target.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const top = document.elementFromPoint(x, y);
        return top && target.contains(top) ? {x, y} : null;
    };
    const models = (available = usable) => Array.from(document.querySelectorAll('[role="menuitemradio"]')).filter(el => {
        if (!available(el)) return false;
        const label = name(el).replace(/^(已选中\s+|Selected\s+)/i, "");
        return label === model || label.startsWith(`${model} `) || label.endsWith(` ${model}`)
            || Array.from(el.querySelectorAll("span, div")).some(child => visible(child) && norm(child.innerText) === model);
    });
    const modeTriggers = (available = usable) => Array.from(document.querySelectorAll('button, [role="button"]'))
        .filter(el => available(el) && /^(打开模式选择器|Open mode selector|打开模型选择器|Open model selector)/i.test(name(el)));
    const modelToken = () => {
        const triggers = modeTriggers();
        if (triggers.length !== 1) return null;
        const label = name(triggers[0]);
        return /当前模式为|当前模型为|current mode|current model/i.test(label) ? label : null;
    };
    const editors = () => matches('textarea, [contenteditable="true"]', el => {
        const label = name(el);
        return /^(为 Gemini 输入提示|Ask Gemini|Enter a prompt for Gemini)$/i.test(label)
            || el.classList.contains("ql-editor");
    });
    const editorText = el => (el instanceof HTMLTextAreaElement ? el.value : el.innerText).replace(/\r\n/g, "\n");
    const referenceInput = () => Array.from(document.querySelectorAll('input[type="file"][accept="image/*,video/*"]'))
        .filter(el => !el.disabled);
    const referenceToken = () => {
        const inputs = editors();
        if (inputs.length !== 1) return null;
        const region = inputs[0].closest('.text-input-field');
        if (!region) return null;
        const previews = Array.from(region.querySelectorAll('uploader-file-preview')).filter(visible);
        if (previews.length !== 1) return null;
        const images = Array.from(previews[0].querySelectorAll('gem-media-attachment img')).filter(visible);
        const close = previews[0].querySelectorAll('.gem-attachment-close-button button');
        const progress = Array.from(region.querySelectorAll('[role="progressbar"], [aria-busy="true"]')).some(visible);
        const send = named(["发送消息", "发送", "Send message", "Send"]);
        if (images.length !== 1 || close.length !== 1 || close[0].matches(':disabled, [aria-disabled="true"]')
                || progress || send.length !== 1) return null;
        const img = images[0], source = img.currentSrc || img.getAttribute('src');
        if (!img.complete || !img.naturalWidth || !img.naturalHeight || !source
                || !/^blob:https:\/\/gemini\.google\.com\//.test(source)) return null;
        return `${source}\n${img.naturalWidth}x${img.naturalHeight}`;
    };
    const selectedTool = () => {
        const labels = kind === "music" ? ["取消选择“音乐”", 'Deselect "Music"', "Cancel music", "close 音乐"]
            : ["取消选择“视频”", 'Deselect "Video"', "Cancel video", "close 视频"];
        return named(labels).length === 1;
    };
    const visibleDialogs = () => Array.from(document.querySelectorAll('dialog, [role="dialog"]')).filter(visible);
    const videoIntro = dialogs => {
        if (kind !== "video" || dialogs.length !== 1) return null;
        const dialog = dialogs[0];
        const title = dialog.getAttribute("aria-label") || document.getElementById(dialog.getAttribute("aria-labelledby"))?.innerText
            || dialog.querySelector("h1,h2,h3,[role=heading]")?.innerText;
        if (!["创作视频", "Create video"].includes(norm(title)) || !/Gemini Omni(?: 加持)?/.test(norm(dialog.innerText))) return null;
        const button = coordinate(Array.from(dialog.querySelectorAll('button')).filter(
            el => usable(el) && ["立即体验", "Get started"].includes(name(el)),
        ));
        return button ? {intro: button} : null;
    };
    const toolEntry = () => {
        const dialogs = visibleDialogs();
        if (dialogs.length) return videoIntro(dialogs);
        return selectedTool() && (kind === "music" || hasVideoControls()) ? {selected: true} : null;
    };
    const videoChoices = {
        duration: [["10 秒", "10 seconds"], ["15 秒", "15 seconds"]],
        aspect: [["横向 (16:9)", "Landscape (16:9)"], ["纵向 (9:16)", "Portrait (9:16)"]],
        format: [["视频", "Video"], ["GIF"]],
    };
    const videoSelection = {
        duration: duration_seconds === 15 ? 1 : 0,
        aspect: aspect_ratio === "9:16" ? 1 : 0,
        format: 0,
    };
    const videoButtons = key => {
        const labeled = matches('button, [role="button"]', el => {
            const text = name(el);
            if (key === "duration") return /^(时长|Duration)[，,:：]/i.test(text);
            if (key === "aspect") return /^(宽高比|Aspect ratio)[，,:：]/i.test(text);
            return key === "format" && (["格式", "Format"].includes(text) || /^(格式|Format)[，,:：]/i.test(text));
        });
        if (key !== "format" || labeled.length) return labeled;
        return matches('button, [role="button"]', el => el.hasAttribute("aria-expanded")
            && ["视频", "Video", "GIF"].includes(name(el)));
    };
    const visibleMenus = () => Array.from(document.querySelectorAll('[role="menu"]')).filter(visible);
    const videoRadios = key => {
        const groups = videoChoices[key];
        if (!groups) return [];
        const matching = visibleMenus().map(menu => Array.from(menu.querySelectorAll('[role="menuitemradio"]'))
            .filter(node => visible(node) && node.closest('[role="menu"]') === menu))
            .filter(nodes => nodes.length === 2 && groups.every(labels => nodes.filter(node => labels.includes(name(node))).length === 1));
        return matching.length === 1 ? matching[0].filter(node => groups[videoSelection[key]].includes(name(node))) : [];
    };
    const hasVideoControls = () => ["duration", "aspect", "format"].every(key => videoButtons(key).length === 1);
    const musicTitles = {duration: ["时长", "Duration"], vocals: ["人声", "Vocals", "Voice"]};
    const musicLabelSuffix = (key, label) => {
        const separator = label.search(/[，,:：]/);
        return separator >= 0 && musicTitles[key]?.includes(norm(label.slice(0, separator)))
            ? norm(label.slice(separator + 1)) : null;
    };
    const musicChoice = (key, label) => {
        const text = norm(label.replace(/[,，]/g, " "));
        if (key === "duration") {
            if (["短版", "Short"].includes(text)) return "short";
            if (["标准", "Standard"].includes(text)) return "standard";
        } else if (key === "vocals") {
            if (["纯音乐", "Instrumental"].includes(text)) return "instrumental";
            if (["开启人声", "人声", "Vocals", "With vocals"].includes(text)) return "vocals";
            if (/^(自定义|Custom)(?:\s|$)/i.test(text) || text === "Auto") return "auto";
        }
        return null;
    };
    const musicButtons = key => matches('button, [role="button"]', el => {
        const label = name(el);
        return musicTitles[key]?.includes(label) || musicLabelSuffix(key, label) !== null || musicChoice(key, label) !== null;
    });
    const musicSelected = {duration: music_duration, vocals: music_vocals};
    const musicRadios = key => {
        const groups = Array.from(document.querySelectorAll('[role="menu"]')).filter(visible)
            .map(menu => Array.from(menu.querySelectorAll('[role="menuitemradio"]'))
                .filter(node => visible(node) && node.closest('[role="menu"]') === menu))
            .filter(nodes => {
                const choices = nodes.map(node => musicChoice(key, name(node)));
                return choices.length >= 2 && choices.every(Boolean) && new Set(choices).size === choices.length
                    && (key === "duration" ? choices.includes("short") && choices.includes("standard")
                        : choices.includes("instrumental") && choices.includes("vocals"));
            });
        return groups.length === 1 ? groups[0].filter(node => musicChoice(key, name(node)) === musicSelected[key]) : [];
    };
    const musicToken = key => {
        const buttons = musicButtons(key);
        if (buttons.length !== 1 || !musicSelected[key]) return null;
        const button = buttons[0];
        const text = norm(button.innerText);
        const choices = [norm(button.getAttribute("aria-label")), text]
            // A bare "Vocals" or "人声" may just label the menu.
            .filter(label => !musicTitles[key].includes(label))
            .map(label => musicChoice(key, musicLabelSuffix(key, label) ?? label))
            .filter(choice => choice !== null);
        return choices.length > 0 && choices.every(choice => choice === musicSelected[key])
            ? `${name(button)}\n${text}` : null;
    };
    const hasMusicControls = () => ["duration", "vocals"].every(key => musicButtons(key).length === 1)
        && matches('button, [role="button"]', el => ["流派", "Genre"].includes(name(el))).length === 1;
    const verifiedMusicControls = () => ["duration", "vocals"].every(key => !musicSelected[key]
        || (typeof music_tokens?.[key] === "string" && music_tokens[key] === musicToken(key)));
    const verifiedVideoControls = () => ["duration", "aspect"].every(key => {
        const buttons = videoButtons(key);
        if (buttons.length !== 1) return false;
        const selection = name(buttons[0]).replace(/^[^，,:：]+[，,:：]\s*/, "");
        return videoChoices[key][videoSelection[key]].includes(selection);
    }) && videoButtons("format").length === 1
        && ["视频", "Video"].includes(name(videoButtons("format")[0]).replace(/^(格式|Format)[，,:：]\s*/i, ""));
    const thinkingLabels = {low: ["低", "Low"], medium: ["中", "Medium"], high: ["高", "High"]};
    const thinkingChoice = node => {
        const label = name(node).replace(/^(已选中\s+|Selected\s+)/i, "");
        const choices = Object.entries(thinkingLabels).filter(([, labels]) =>
            labels.some(value => label === value || label.startsWith(`${value} `)));
        return choices.length === 1 ? choices[0][0] : null;
    };
    const thinkingRadios = () => {
        const labels = thinkingLabels[thinking_level];
        if (!labels) return [];
        const menus = Array.from(document.querySelectorAll('[role="menu"]')).filter(visible);
        const groups = menus.map(menu => Array.from(menu.querySelectorAll('[role="menuitemradio"]'))
            .filter(node => usable(node) && thinkingChoice(node)))
            .filter(nodes => nodes.length === 3 && new Set(nodes.map(thinkingChoice)).size === 3);
        const matching = groups.filter((nodes, index) =>
            groups.findIndex(other => other.length === nodes.length && nodes.every(node => other.includes(node))) === index);
        return matching.length === 1 ? matching[0].filter(node => thinkingChoice(node) === thinking_level) : [];
    };
    const media = () => Array.from(document.querySelectorAll("audio, video")).flatMap(el => {
        const isVideo = el.localName === "video";
        if (kind === "video" && !isVideo) return [];
        // Music can arrive as a video player with cover art and a generated audio track.
        if (kind === "music" && isVideo) {
            const musicOutput = el.closest(musicOutputSelector);
            if (!musicOutput?.closest(responseRegionSelector) || el.closest(userQuerySelector)) return [];
        }
        const source = el.querySelector("source[src]");
        const location = el.currentSrc || el.getAttribute("src") || source?.getAttribute("src");
        if (!location || location.length > 8192) return [];
        const mime = source?.getAttribute("type") || el.getAttribute("type");
        return [{kind, url: location, mime_type: mime,
            width: el.videoWidth || null, height: el.videoHeight || null,
            duration: Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null,
            ready: el.readyState >= 1 && !el.error && (!isVideo || (el.videoWidth > 0 && el.videoHeight > 0))}];
    }).slice(0, 12);

    if (action === "status") {
        const seen = window.__geminiNativeMediaNodes ||= {ids: new WeakMap(), next: 0};
        const signals = Array.from(document.querySelectorAll(`${responseSelector}, [role="alert"]`)).flatMap(el => {
            if (!visible(el) || el.querySelector("audio,video") || el.closest(userQuerySelector)) return [];
            const value = norm(el.innerText);
            const quotedStart = (() => {
                const selector = "blockquote, pre, code, q";
                if (!el.querySelectorAll(selector).length) return false;
                if (typeof document.createTreeWalker !== "function") return true;
                const walker = document.createTreeWalker(el, 4); // SHOW_TEXT, in document order.
                for (let node; (node = walker.nextNode());) {
                    if (!norm(node.nodeValue) || !node.parentElement || !visible(node.parentElement)) continue;
                    return Boolean(node.parentElement.closest(selector));
                }
                return true;
            })();
            const musicRefusal = kind === "music" && el.matches(responseSelector) && !quotedStart
                && (/^I don['’]t have a built-in audio generation tool available right now to render a playable sound file directly\b/i.test(value)
                    || /^I can['’]t generate or output an audio file directly right now(?: because|, as) the music generation tool isn['’]t available in this session\.(?:\s|$)/i.test(value)
                    || /^I cannot directly generate or output playable audio files here, as the music generation tool is currently unavailable\.(?:\s|$)/i.test(value));
            const generationError = el.matches(responseSelector) && !quotedStart
                && (/(?:^|\n)I seem to be encountering an error\.(?:\s|$)/i.test(value)
                    || /(?:^|\n)I encountered an error doing what you asked\. Could you try again\?(?:\s|$)/i.test(value)
                    || /(?:^|\n)Sorry, something went wrong\. Please try your request again\.(?:\s|$)/i.test(value)
                    || /(?:^|\n)I['’]m having a hard time fulfilling your request\. Can I help you with something else instead\?(?:\s|$)/i.test(value)
                    || value === "由于程序代码的局限，我无法提供这方面的帮助。"
                    || (kind === "video" && (
                        /(?:^|\n)I can['’]t make (?:that type of video|videos?\b[^.?!]{0,200})\.(?:\s|$)/i.test(value)
                        || /(?:^|\n)我只是一个文本 AI[，,]在这方面没法帮到你[。.]?(?:\s|$)/.test(value)
                    )));
            if (!musicRefusal && !generationError && value.length > 450) return [];
            const quota = /^(?:I can create more (?:videos|music|songs|audio) as soon as your limit resets\b|You(?:'ve| have) reached (?:your|the) (?:video|music|audio) generation limit\b|(?:你|您)已达到(?:视频|音乐|音频)生成(?:次数)?上限|(?:视频|音乐|音频)生成(?:次数)?已达上限)/i.test(value);
            const capacity = /^(?:I couldn['’]t do that because I['’]m getting a lot of requests right now\. Please try again later\.?|I['’]m sorry,\s*I['’]m experiencing unusually high traffic right now generating (?:videos|music|images) and am at full capacity\. Please try again later\.?|(?:现在|目前)的请求过多[，。,. ]*请稍后再试[。.]?)$/i.test(value);
            if (!musicRefusal && !generationError && !quota && !capacity) return [];
            if (!seen.ids.has(el)) seen.ids.set(el, ++seen.next);
            return [{id: seen.ids.get(el), code: musicRefusal ? "ARTIFACT_NOT_RETURNED"
                : generationError ? "UPSTREAM_REJECTED" : "UPSTREAM_LIMIT_EXCEEDED"}];
        });
        return {media: media(), signals};
    }

    if (action === "load_blob") {
        if (!url.startsWith(`blob:${gemini}/`) || !media().some(el => el.ready && el.url === url)) return null;
        const player = Array.from(document.querySelectorAll("audio,video")).find(el => el.currentSrc === url);
        if (!player) return null;
        const response = await fetch(url);
        if (!response.ok || !response.body) return null;
        const reader = response.body.getReader();
        const chunks = [];
        let total = 0;
        try {
            for (;;) {
                const {done, value} = await reader.read();
                if (done) break;
                total += value.length;
                if (total > 100 * 1024 * 1024) return null;
                chunks.push(value);
            }
        } finally {
            await reader.cancel();
        }
        window.__geminiNativeMediaBlob = new Blob(chunks, {type: response.headers.get("content-type") || ""});
        return {size: total, mime_type: window.__geminiNativeMediaBlob.type};
    }
    if (action === "blob_chunk") {
        const blob = window.__geminiNativeMediaBlob;
        if (!(blob instanceof Blob) || !Number.isInteger(offset) || offset < 0 || !Number.isInteger(size)
                || size <= 0 || size > 64 * 1024 || offset + size > blob.size) return null;
        const content = new Uint8Array(await blob.slice(offset, offset + size).arrayBuffer());
        let binary = "";
        for (let i = 0; i < content.length; i++) binary += String.fromCharCode(content[i]);
        return btoa(binary);
    }
    if (action === "clear_blob") {
        delete window.__geminiNativeMediaBlob;
        return true;
    }

    if (!atComposer) return null;
    if (["editor", "editor_focused", "send", "reference_input", "reference_token", "composer_menu_closed"].includes(action)
            && Array.from(document.querySelectorAll('dialog, [role="dialog"]')).some(visible)) return null;
    if (action === "tools") return coordinate(named(["上传和工具", "Upload and tools", "Tools"]));
    if (action === "tooltip_dismiss") {
        if (visibleDialogs().length || visibleMenus().length) return null;
        const discovery = Array.from(document.querySelectorAll('gem-discovery-tooltip, .gem-discovery-tooltip-container'))
            .some(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden");
        if (discovery) {
            const point = coordinate(editors());
            return point ? {...point, click: true} : null;
        }
        // Angular can leave an aria-hidden tooltip intercepting pointer hits.
        const tooltip = Array.from(document.querySelectorAll('[role="tooltip"], .mat-mdc-tooltip-surface'))
            .some(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden");
        return tooltip ? coordinate(editors())
            || coordinate(named(["上传和工具", "Upload and tools", "Tools"])) : null;
    }
    if (action === "reference_input") return kind === "video" && reference_required && referenceInput().length === 1;
    if (action === "reference_token") return kind === "video" && reference_required ? referenceToken() : null;
    if (action === "composer_menu_closed") return visibleMenus().length === 0 && visibleDialogs().length === 0;
    if (action === "tool_unselected") {
        const inputs = editors();
        const chip = Array.from(document.querySelectorAll('button, [role="button"]')).some(el => visible(el)
            && /^(?:close\s|Deselect\s|Cancel\s|取消选择|关闭(?:音乐|视频))/i.test(name(el)));
        return !chip && !visibleDialogs().length && inputs.length === 1 && !editorText(inputs[0]).trim();
    }
    if (action === "more") return coordinate(named(["更多工具", "More tools"]));
    if (action === "media_tool") return coordinate(named(kind === "music" ? ["制作音乐", "创作音乐", "Create music"] : ["制作视频", "Create video"]));
    if (action === "tool_entry") return toolEntry();
    if (action === "tool_selected") return selectedTool();
    if (action === "video_intro_closed") return kind === "video" && visibleDialogs().length === 0;
    if (action === "music_options") return kind === "music" && hasMusicControls();
    if (action === "music_option_menu") return kind === "music" ? coordinate(musicButtons(option)) : null;
    if (action === "music_option_target") return kind === "music" ? coordinate(musicRadios(option).filter(usable)) : null;
    if (action === "music_option_checked") return kind === "music" && musicRadios(option).length === 1
        && musicRadios(option)[0].getAttribute("aria-checked") === "true";
    if (action === "music_option_token") return kind === "music" ? musicToken(option) : null;
    if (kind === "video" && (action === "video_options" || action.startsWith("video_option_"))) {
        const dialogs = visibleDialogs();
        if (dialogs.length) return videoIntro(dialogs);
        if (action === "video_options") return selectedTool() && hasVideoControls();
        const buttons = videoButtons(option);
        if (action === "video_option_menu") return coordinate(buttons);
        const expanded = buttons.length === 1 ? buttons[0].getAttribute("aria-expanded") : null;
        if (action === "video_option_closed") return expanded === "false" && visibleMenus().length === 0;
        const radios = videoRadios(option);
        if (action === "video_option_open") return expanded === "true" && radios.length === 1;
        if (action === "video_option_target") return coordinate(radios.filter(usable));
        if (action === "video_option_checked") return radios.length === 1 && radios[0].getAttribute("aria-checked") === "true";
    }
    if (action === "mode") return coordinate(modeTriggers());
    if (action === "pick_model") return coordinate(models());
    if (action === "model_menu_open") {
        const triggers = modeTriggers();
        if (triggers.length !== 1 || triggers[0].getAttribute("aria-expanded") !== "true") return null;
        const radios = models(visible);
        return radios.length === 1 ? coordinate(radios.filter(usable)) : null;
    }
    if (action === "model_checked") return models().length === 1 && models()[0].getAttribute("aria-checked") === "true"
        ? modelToken() : null;
    if (action === "model_menu_closed") {
        const triggers = modeTriggers();
        return triggers.length === 1 && triggers[0].getAttribute("aria-expanded") === "false" && models(visible).length === 0;
    }
    if (action === "model_menu_stale") {
        const triggers = modeTriggers(visible);
        if (triggers.length !== 1 || !usable(triggers[0]) || triggers[0].getAttribute("aria-expanded") !== "true"
                || visibleMenus().length || visibleDialogs().length || models(visible).length) return null;
        const controls = (triggers[0].getAttribute("aria-controls") || "").trim().split(/\s+/).filter(Boolean);
        if (controls.some(id => {
            const panel = document.getElementById(id);
            if (!panel?.getClientRects().length) return false;
            const style = getComputedStyle(panel);
            return style.visibility === "visible" && style.opacity !== "0";
        })) return null;
        return coordinate(triggers);
    }
    if (action === "thinking_menu") return coordinate(named(["思考等级", "思考级别", "Thinking level"]));
    if (action === "thinking_target") return coordinate(thinkingRadios());
    if (action === "thinking_checked") return thinkingRadios().length === 1
        && thinkingRadios()[0].getAttribute("aria-checked") === "true";
    if (action === "editor" || action === "editor_focused") {
        const inputs = editors();
        if (inputs.length !== 1 || editorText(inputs[0]).trim()) return action === "editor_focused" ? false : null;
        return action === "editor" ? coordinate(inputs) : document.activeElement === inputs[0];
    }
    if (action === "send") {
        const input = editors();
        if (reference_required && (!reference_token || referenceToken() !== reference_token)) return null;
        if (!reference_required && Array.from(document.querySelectorAll('uploader-file-preview')).some(visible)) return null;
        if (kind === "music" && !verifiedMusicControls()) return null;
        if (!model_token || modelToken() !== model_token || !selectedTool() || input.length !== 1 || editorText(input[0]) !== prompt) return null;
        if (kind === "video" && !verifiedVideoControls()) return null;
        if (kind === "music" && !hasMusicControls()) return null;
        return coordinate(named(["发送消息", "发送", "Send message", "Send"]));
    }
    return null;
}
