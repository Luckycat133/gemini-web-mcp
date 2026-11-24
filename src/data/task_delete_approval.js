async ({taskId, responseId, title}) => {
    const notMatched = {status: "not_matched"};
    if (typeof taskId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(taskId)
            || typeof responseId !== "string" || !/^r_[A-Za-z0-9_-]{1,128}$/.test(responseId)
            || typeof title !== "string" || !title || title !== title.trim()) {
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
    const cardSelector = "action-card remy-confirmation-card-v2";
    const buttonSelector = ".button-group button";
    const questionText = `确定要删除“${title}”吗？`;

    function findApproval() {
        const responses = document.querySelectorAll(responseSelector);
        if (responses.length !== 1 || !isVisible(responses[0])) {
            return null;
        }
        const response = responses[0];
        const questions = Array.from(response.querySelectorAll("p")).filter(
            (paragraph) => isVisible(paragraph) && !paragraph.closest("action-card"),
        );
        if (questions.length !== 1 || questions[0].innerText.trim() !== questionText) {
            return null;
        }

        const activeCards = Array.from(document.querySelectorAll(cardSelector)).filter(
            (card) => Array.from(card.querySelectorAll(buttonSelector)).some(isEnabled),
        );
        if (activeCards.length !== 1 || !response.contains(activeCards[0])) {
            return null;
        }
        const banners = activeCards[0].querySelectorAll("gem-banner");
        if (banners.length !== 1 || !isVisible(banners[0])) {
            return null;
        }
        const banner = banners[0];
        const headings = banner.querySelectorAll("h3");
        const deletePrompts = Array.from(banner.querySelectorAll("p")).filter(
            (paragraph) => isVisible(paragraph)
                && paragraph.innerText.replace(/\s+/g, "") === "要允许Gemini删除任务吗？",
        );
        if (headings.length !== 1 || !isVisible(headings[0]) || headings[0].innerText.trim() !== "Google Tasks"
                || deletePrompts.length !== 1) {
            return null;
        }

        const buttons = Array.from(banner.querySelectorAll(buttonSelector));
        if (buttons.length !== 2 || !buttons.every(isEnabled)) {
            return null;
        }
        const approve = buttons.find((button) => button.innerText.trim() === "允许"
            && button.matches("gem-button.primary-button > button"));
        const deny = buttons.find((button) => button.innerText.trim() === "不允许");
        return approve && deny ? approve : null;
    }

    const deadline = performance.now() + 6000;
    while (onTaskPage()) {
        const approve = findApproval();
        if (approve) {
            // Selection and activation happen in the same document turn.
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
