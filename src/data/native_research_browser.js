async ({action, model, model_token, prompt, thinking_level}) => {
    const gemini = "https://gemini.google.com";
    if (location.origin !== gemini || window !== window.top) return null;
    const atComposer = location.pathname === "/app" || location.pathname === "/app/";
    const responseSelector = 'model-response, [id^="model-response-message-content"], [data-model-response-turn-id]';
    const userQuerySelector = 'user-query, [data-test-id="user-query"]';
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
    const candidateModelNames = (() => {
        const raw = norm(model);
        if (!raw) return [];
        const base = raw.replace(/^gemini[-\s]?(?:4[-\s]?)?/i, "").trim();
        const vals = new Set([raw, raw.replace(/^gemini[-\s]?/i, "")]);
        if (base && !base.startsWith("4 ")) vals.add(`4 ${base}`);
        if (/^4\s+/i.test(raw)) vals.add(raw.replace(/^4\s+/i, ""));
        return Array.from(vals).map(v => v.toLowerCase());
    })();
    const models = (available = usable) => Array.from(document.querySelectorAll('[role="menuitemradio"]')).filter(el => {
        if (!available(el) || !candidateModelNames.length) return false;
        const label = name(el).replace(/^(已选中\s+|Selected\s+)/i, "").toLowerCase();
        if (candidateModelNames.some(c => label === c || label.startsWith(`${c} `) || label.endsWith(` ${c}`))) return true;
        return Array.from(el.querySelectorAll("span, div")).some(
            child => visible(child) && candidateModelNames.includes(norm(child.innerText).toLowerCase()),
        );
    });
    const modeTriggers = (available = usable) => Array.from(document.querySelectorAll('button, [role="button"]'))
        .filter(el => available(el) && /^(打开模式选择器|Open mode selector|打开模型选择器|Open model selector)/i.test(name(el)));
    const modelToken = () => {
        const triggers = modeTriggers();
        if (triggers.length !== 1) return null;
        const label = name(triggers[0]);
        return /当前模式为|当前模型为|current mode|current model/i.test(label) ? label : null;
    };
    const editors = () => matches('textarea, [contenteditable="true"], [role="textbox"]', el => {
        const label = name(el);
        return /^(为 Gemini 输入提示|问问 Gemini|问问 Deep Research|Ask Gemini|Enter a prompt for Gemini|Ask Deep Research)/i.test(label)
            || el.classList.contains("ql-editor");
    });
    const editorText = el => (el instanceof HTMLTextAreaElement ? el.value : el.innerText).replace(/\r\n/g, "\n");
    const visibleDialogs = () => Array.from(document.querySelectorAll('dialog, [role="dialog"]')).filter(visible);
    const visibleMenus = () => Array.from(document.querySelectorAll('[role="menu"], .mat-mdc-menu-panel')).filter(visible);
    const thinkingRadios = () => {
        const labels = {
            low: ["低", "标准", "Low", "Standard"],
            medium: ["中", "Medium"],
            high: ["高", "扩展", "High", "Extended"],
        }[thinking_level] || [];
        return matches('[role="menuitemradio"]', el => {
            const label = name(el).replace(/^(已选中\s+|Selected\s+)/i, "");
            return labels.includes(label)
                || Array.from(el.querySelectorAll("span, div")).some(child => visible(child) && labels.includes(norm(child.innerText)));
        });
    };
    const selectedResearchTool = () => Array.from(document.querySelectorAll('button, [role="button"]')).some(el => {
        if (!visible(el)) return false;
        const label = name(el);
        return /^(?:取消选择|Deselect|Cancel|close).*?(?:Deep Research|深度研究)/i.test(label);
    });
    const confirmButtons = () => matches('button, [role="button"]', el => {
        if (el.closest(userQuerySelector)) return false;
        return /^(开始研究|Start research|Start)$/i.test(name(el));
    });
    const stripSpeakerPrefix = value => value.replace(/^(?:(?:Gemini\s*说|Gemini\s+said)\s+)?(?:(?:显示思路|Show\s+thinking)\s+)?/i, "").trim();

    if (action === "status") {
        const confirms = confirmButtons();
        const confirmCoord = confirms.length === 1 ? coordinate(confirms) : null;
        const headings = Array.from(document.querySelectorAll(
            `${responseSelector} h1, ${responseSelector} h2, ${responseSelector} h3, [class*="research"] h1, [class*="research"] h2`
        )).filter(visible).map(el => norm(el.innerText)).filter(Boolean);
        const progress = Boolean(document.querySelector(
            'deep-research-immersive-panel, immersive-panel, [data-test-id="research-progress"], [data-test-id="stop-button"], button[aria-label*="停止回复"], button[aria-label*="Stop generating"]'
        ));
        let refusal = null;
        if (!confirms.length && !progress) {
            for (const el of Array.from(document.querySelectorAll(responseSelector))) {
                if (!visible(el) || el.closest(userQuerySelector)) continue;
                const txt = stripSpeakerPrefix(norm(el.innerText));
                if (txt.length <= 220 && (
                    /^我(?:只?是)?一个(?:语言模型|文本\s*AI)/.test(txt)
                    || /^我没法提供(?:这方面的)?帮助/.test(txt)
                    || /^请恕我爱莫能助/.test(txt)
                    || /^由于程序代码的局限/.test(txt)
                    || /^I(?:'m| am) (?:just )?a (?:language model|text-based AI)/i.test(txt)
                    || /^I (?:can't|cannot) assist with that/i.test(txt)
                    || /^I seem to be encountering an error\./i.test(txt)
                )) {
                    refusal = txt;
                    break;
                }
            }
        }
        return {
            confirm_button: confirmCoord,
            confirm_present: confirms.length > 0,
            plan_title: headings[0] || null,
            progress,
            refusal,
        };
    }

    if (!atComposer) return null;
    if (["editor", "editor_focused", "send", "composer_menu_closed"].includes(action) && visibleDialogs().some(visible)) return null;
    if (action === "tools") return coordinate(named(["上传和工具", "Upload and tools", "Tools"]));
    if (action === "tooltip_dismiss") {
        if (visibleDialogs().length || visibleMenus().length) return null;
        const discovery = Array.from(document.querySelectorAll('gem-discovery-tooltip, .gem-discovery-tooltip-container'))
            .some(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden");
        if (discovery) {
            const point = coordinate(editors());
            return point ? {...point, click: true} : null;
        }
        const tooltip = Array.from(document.querySelectorAll('[role="tooltip"], .mat-mdc-tooltip-surface'))
            .some(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden");
        return tooltip ? coordinate(editors()) || coordinate(named(["上传和工具", "Upload and tools", "Tools"])) : null;
    }
    if (action === "more") return coordinate(named(["更多工具", "More tools"]));
    if (action === "research_tool") return coordinate(named(["Deep Research", "深度研究"]));
    if (action === "tool_selected") return selectedResearchTool();
    if (action === "composer_menu_closed") return visibleMenus().length === 0 && visibleDialogs().length === 0;
    if (action === "mode") return coordinate(modeTriggers());
    if (action === "model_menu_open") {
        const triggers = modeTriggers();
        if (triggers.length !== 1 || triggers[0].getAttribute("aria-expanded") !== "true") return null;
        if (!candidateModelNames.length) return {open: true};
        const radios = models(visible);
        return radios.length === 1 ? coordinate(radios.filter(usable)) : {open: true};
    }
    if (action === "model_checked") {
        if (!candidateModelNames.length) return modelToken();
        return models().length === 1 && models()[0].getAttribute("aria-checked") === "true" ? modelToken() : null;
    }
    if (action === "model_menu_closed") {
        const triggers = modeTriggers();
        return triggers.length === 1 && triggers[0].getAttribute("aria-expanded") === "false" && models(visible).length === 0;
    }
    if (action === "thinking_menu") return coordinate(named(["思考等级", "思考级别", "Thinking level"]));
    if (action === "thinking_target") return coordinate(thinkingRadios());
    if (action === "thinking_checked") return thinkingRadios().length === 1 && thinkingRadios()[0].getAttribute("aria-checked") === "true";
    if (action === "editor" || action === "editor_focused") {
        const inputs = editors();
        if (inputs.length !== 1 || editorText(inputs[0]).trim()) return action === "editor_focused" ? false : null;
        return action === "editor" ? coordinate(inputs) : document.activeElement === inputs[0];
    }
    if (action === "send") {
        const input = editors();
        if (!selectedResearchTool() || input.length !== 1 || editorText(input[0]) !== prompt) return null;
        if (model_token && modelToken() && modelToken() !== model_token) return null;
        return coordinate(named(["发送消息", "发送", "Send message", "Send"]));
    }
    return null;
}
