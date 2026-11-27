async ({taskId, responseId}) => {
    const notMatched = {status: "not_matched"};
    if (typeof taskId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(taskId)
            || typeof responseId !== "string" || !/^r_[A-Za-z0-9_-]{1,128}$/.test(responseId)) {
        return notMatched;
    }

    const onTaskPage = () => location.origin === "https://gemini.google.com"
        && location.pathname === `/app/${taskId}`;
    const isVisible = (element) => {
        if (!element.isConnected || !element.getClientRects().length
                || element.closest('[hidden], [inert], [aria-hidden="true"]')) {
            return false;
        }
        const style = getComputedStyle(element);
        return style.visibility === "visible" && style.display !== "none" && style.opacity !== "0";
    };
    const isEnabled = (button) => isVisible(button) && !button.matches(':disabled, [aria-disabled="true"]');
    const responseSelector = `div#model-response-message-content${responseId}`;
    const cardSelector = "action-card remy-confirmation-card-v2, action-card";
    const buttonSelector = ".button-group button, gem-button button, button";
    const denyLabels = new Set([
        "deny", "do not allow", "don't allow", "cancel", "reject", "no", "decline", "skip", "stop",
        "不允许", "拒绝", "取消", "否", "不允許", "拒絕",
    ]);
    const preferredLabels = [
        "允许一次", "allow once",
        "允许", "allow",
        "确认", "confirm",
        "继续", "continue", "proceed",
        "批准", "approve",
        "同意", "accept", "agree",
        "确定", "ok", "yes",
        "执行", "run", "submit", "authorize",
    ];

    function findApprovalButton() {
        const responses = document.querySelectorAll(responseSelector);
        if (responses.length !== 1 || !isVisible(responses[0])) {
            return null;
        }
        const response = responses[0];
        const cards = Array.from(response.querySelectorAll(cardSelector)).filter(
            (card) => Array.from(card.querySelectorAll(buttonSelector)).some(isEnabled),
        );
        if (!cards.length) {
            return null;
        }
        const buttons = Array.from(cards[cards.length - 1].querySelectorAll(buttonSelector)).filter(isEnabled);
        if (!buttons.length) {
            return null;
        }
        const candidates = buttons.filter((button) => {
            const label = (button.innerText || button.textContent || "").trim().toLowerCase().replace(/\s+/g, " ");
            return label && !denyLabels.has(label);
        });
        if (!candidates.length) {
            return null;
        }
        for (const target of preferredLabels) {
            const matched = candidates.find((button) => {
                const label = (button.innerText || button.textContent || "").trim().toLowerCase().replace(/\s+/g, " ");
                return label === target;
            });
            if (matched) {
                return matched;
            }
        }
        const primary = candidates.find((button) => button.closest("gem-button.primary-button, .primary-button"));
        return primary || candidates[0];
    }

    const deadline = performance.now() + 6000;
    while (onTaskPage()) {
        const approve = findApprovalButton();
        if (approve) {
            approve.click();
            await new Promise((resolve) => setTimeout(resolve, 1000));
            return {status: "activated"};
        }
        if (performance.now() >= deadline) {
            break;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return notMatched;
}
