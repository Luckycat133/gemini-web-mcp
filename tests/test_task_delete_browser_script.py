"""Exercise the native approval selector against disposable DOM fixtures."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).parents[1] / "src" / "data" / "task_delete_approval.js"
NODE = shutil.which("node")

# Supply the observed native selectors and enough DOM behavior to vary the
# location, response, target, permissions, and render timing independently.
FIXTURE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const scenario = process.argv[2];
const taskId = 'demo123';
const responseId = 'r_latest';
const title = 'Disposable item';
const responseSelector = `div#model-response-message-content${responseId}`;
const cardSelector = 'action-card remy-confirmation-card-v2';
const buttonSelector = '.button-group button';
let clicks = 0;
let priorClicks = 0;
let ticks = 0;
let clock = 0;

function element({text = '', hidden = false, inCard = false, disabled = false,
                  primary = false, selectors = {}, contains = () => false, click = () => {}} = {}) {
    return {
        innerText: text,
        isConnected: true,
        getClientRects: () => hidden ? [] : [{}],
        closest: (selector) => selector === 'action-card' && inCard ? {} : null,
        matches: (selector) => selector === ':disabled, [aria-disabled="true"]' ? disabled : primary,
        querySelectorAll: (selector) => selectors[selector] || [],
        contains,
        click,
    };
}

function makeCard({prior = false, active = true} = {}) {
    const approve = element({text: '允许', primary: scenario !== 'no_primary',
        disabled: !active || scenario === 'disabled', click: () => prior ? priorClicks++ : clicks++});
    const deny = element({text: scenario === 'wrong_deny' ? '稍后处理' : '不允许', disabled: !active});
    const heading = element({text: scenario === 'wrong_app' ? 'Google Sheets' : 'Google Tasks'});
    const permission = element({text: scenario === 'different_permission'
        ? '要允许 Gemini 管理任务吗？' : '要允许 Gemini 删除任务吗？', inCard: true});
    const banner = element({hidden: scenario === 'hidden_banner', selectors: {
        [buttonSelector]: [deny, approve], h3: [heading], p: [permission],
    }});
    return element({selectors: {[buttonSelector]: [deny, approve], 'gem-banner': [banner]}});
}

const card = makeCard();
const prior = makeCard({prior: true, active: scenario === 'two_active_cards'});
const prompt = element({text: scenario === 'different_item'
    ? '确定要删除“Another item”吗？' : `确定要删除“${title}”吗？`});
const response = element({selectors: {p: [prompt, element({text: 'Native card detail', inCard: true})]},
    contains: (element) => element === card});
const location = {
    origin: scenario === 'wrong_origin' ? 'https://example.com' : 'https://gemini.google.com',
    pathname: scenario === 'wrong_task' ? '/app/other' : `/app/${taskId}`,
};
const document = {querySelectorAll(selector) {
    if (selector === responseSelector) {
        if (scenario === 'late_render' && ticks < 2) return [];
        if (scenario === 'duplicate_response') return [response, response];
        return [response];
    }
    return selector === cardSelector ? [prior, card] : [];
}};
const context = vm.createContext({
    location, document, performance: {now: () => clock},
    getComputedStyle: () => ({visibility: 'visible', display: 'block', opacity: '1'}),
    setTimeout: (callback, milliseconds) => {
        clock += milliseconds;
        ticks++;
        if (scenario === 'navigation_after_read') location.pathname = '/app/other';
        return queueMicrotask(callback);
    },
});
if (scenario === 'navigation_after_read') {
    const original = document.querySelectorAll;
    document.querySelectorAll = (selector) => ticks === 0 ? [] : original(selector);
}
const run = vm.runInContext(`(${fs.readFileSync(process.argv[1], 'utf8')})`, context);
run({taskId, responseId: scenario === 'wrong_response' ? 'r_old' : responseId, title}).then((result) => {
    process.stdout.write(JSON.stringify({result, clicks, priorClicks}));
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to execute the bundled JavaScript fixture")
@pytest.mark.parametrize(
    ("scenario", "activated"),
    [
        ("exact_match", True),
        ("late_render", True),
        ("wrong_origin", False),
        ("wrong_task", False),
        ("wrong_response", False),
        ("duplicate_response", False),
        ("different_item", False),
        ("wrong_app", False),
        ("different_permission", False),
        ("two_active_cards", False),
        ("hidden_banner", False),
        ("no_primary", False),
        ("disabled", False),
        ("wrong_deny", False),
        ("navigation_after_read", False),
    ],
)
def test_native_delete_approval_matches_the_exact_current_card(scenario, activated):
    completed = subprocess.run(
        [NODE, "-e", FIXTURE, str(SCRIPT), scenario], capture_output=True, check=True, timeout=5,
    )
    assert json.loads(completed.stdout) == {
        "result": {"status": "activated" if activated else "not_matched"},
        "clicks": int(activated),
        "priorClicks": 0,
    }
