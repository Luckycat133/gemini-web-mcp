"""Run only local fake-DOM JavaScript; never launch Chrome or contact an account."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_native_media_browser import _DOM_FIXTURE

_NOTICE = "由于程序代码的局限，我无法提供这方面的帮助。"


@pytest.fixture(scope="module")
def chinese_failure_signals():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the bundled local DOM contract")
    program = _DOM_FIXTURE.split("(async () => {", 1)[0] + "\nconst notice = " + json.dumps(_NOTICE) + ";\n" + r"""
(async () => {
    const result = {};
    for (const kind of ['video', 'music']) {
        const fixtures = {
            direct: element({text: notice, response: true}),
            react: element({text: notice, reactResponse: true}),
            whitespace: element({text: ` \n${notice}\n `, response: true}),
            quotation: element({text: `“${notice}”`, response: true}),
            discussion: element({text: `模型可能回答：${notice}`, response: true}),
            instruction: element({text: `请回复：${notice}`, response: true}),
            history: element({text: `此前的错误是：${notice}`, response: true}),
            suffix: element({text: `${notice}这只是一个示例。`, response: true}),
            uri: element({text: `${notice} https://example.invalid/video.mp4`, response: true}),
            user: element({text: notice, response: true, question: true}),
            alert: element({text: notice}),
            media: element({text: notice, response: true, selectors: {'audio,video': [{}]}}),
        };
        for (const tag of ['blockquote', 'pre', 'code', 'q']) {
            const quote = element({text: notice, quoted: true});
            fixtures[tag] = element({text: notice, response: true,
                selectors: {'blockquote, pre, code, q': [quote]},
                textNodes: [{nodeValue: notice, parentElement: quote}]});
        }
        result[kind] = {};
        for (const [key, value] of Object.entries(fixtures)) {
            responses = [value];
            result[kind][key] = (await call('status', {kind})).signals.map(signal => signal.code);
        }
    }
    console.log(JSON.stringify(result));
})().catch(error => { console.error(error.name); process.exit(1); });
"""
    script = Path(__file__).parents[1] / "src/data/native_media_browser.js"
    result = subprocess.run([node, "-e", program, str(script)], check=True, capture_output=True, text=True, timeout=5)
    return json.loads(result.stdout)


@pytest.mark.parametrize("kind", ["video", "music"])
def test_chinese_failure_dom_matches_only_whole_direct_model_reply(chinese_failure_signals, kind):
    signals = chinese_failure_signals[kind]
    assert all(signals[key] == ["UPSTREAM_REJECTED"] for key in ("direct", "react", "whitespace"))
    assert all(codes == [] for key, codes in signals.items() if key not in {"direct", "react", "whitespace"})
