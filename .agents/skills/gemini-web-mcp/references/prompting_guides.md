# Gemini Web Multimodal Prompting Guides

Use these structured blueprints when drafting prompts for Gemini Web's `image`, `video`, and `music` tools.

> **Safety & Sensitive Prompts Rule**: Do **not** pre-filter, censor, or refuse prompts locally using any sensitive-word blocklist. Send the user's intended prompt directly. Only if Gemini Web returns `UPSTREAM_REJECTED` should the agent rephrase and retry.

---

## 1. Image Generation & Editing (`image`)

Modern multimodal image synthesis follows natural descriptive sentences rather than comma-separated `"masterpiece, 8k, ultra detailed"` tag spam.

### Six-Component Image Formula
Put the highest-priority elements first:

`[Subject] + [Action / Relationships] + [Setting & Environment] + [Style / Medium] + [Composition & Camera] + [Lighting & Color]`

1. **Subject**: Concrete materials, expressions, attire, age, and naming (`"a weathered brass compass resting beside a hand-drawn nautical chart"`).
2. **Action / Relationships**: Pose, motion, or spatial interaction (`"Sarah hands an unopened linen envelope to Leo across the wooden counter"`).
3. **Setting**: Location, time of day, weather, and depth (`"inside a sunlit botanical conservatory filled with mist and tropical ferns"`).
4. **Style / Medium**: Explicit visual medium (`"35mm editorial film photograph"`, `"clean Swiss minimalist vector poster"`, `"watercolor architectural sketch"`).
5. **Composition & Camera**: Framing and lens (`"wide 16:9 establishing shot, shallow depth of field, eye-level 50mm prime lens"`).
6. **Lighting & Color**: Light direction and palette (`"soft golden hour backlighting, warm amber rim light, cool slate shadows"`).

### Best Practices
- **Positive Framing**: Describe what you *want* (`"a quiet, deserted cobblestone street at dawn"`) rather than negative exclusions (`"no cars, no people"`).
- **Exact Typography**: Wrap rendered text in double quotation marks (`"a vintage cream poster reading 'AUTUMN JAZZ FESTIVAL' in bold serif lettering"`).
- **Multi-Reference & Edits (`image_paths` / `source_operation_id`)**: Reference attached images explicitly by order (`"Use the character from Image 1 and the jacket texture from Image 2..."`), or specify targeted deltas when iterating via `source_operation_id` (`"Keep the pavilion and pine trees unchanged; add a warm glowing paper lantern beneath the eaves"`).
- **Aspect Ratios**: Pass `aspect_ratio` explicitly (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`).
- **Film Pre-Production (sheets → sets → keyframes)**: For multi-shot films, first generate one character sheet per recurring person (front, three-quarter, and profile views, plus full body) and one reference per location. Then build every shot's keyframe with `image_paths=[sheet(s)..., set]` and a prompt that names each image's role (`"Image 1 is a character sheet, Image 2 is the set. Medium shot of the young man from the character sheet (same face, wet dark hair, navy sweater)…"`). Open each keyframe and compare the face with the sheet before you spend video credits on it. Prefer **2 reference images per keyframe**. With three (two sheets plus a set), 3 of 6 shots failed all three attempts with `partial` and no artifact, and all three passed on the first try with two sheets and the set described in text. The set reference must be shot from **where the characters stand**. With an exterior or aerial set image, the model places the character on a separate platform looking at the set from outside.

---

## 2. Video Generation (`video` = Gemini Omni 1.2 Flash)

Parameters: `duration_seconds` (`10` or `15`), `aspect_ratio` (`16:9` or `9:16`), optional first-frame `image_path` (one image, `10`s only). Output observed on 2026-11-28: 1280×720, 24 fps MP4 with a native audio track. The Gemini Web surface has no negative-prompt field, no multi-image reference, and no edit/extend tool, so to change a clip you regenerate it.

### Director's Brief Order
Write English prose in this order. The model reads the image's role and the generation mode from natural language, so do not use control tags such as `<FIRST_FRAME>`:

`[Image role] + [Shot form: single continuous shot or timecoded beats] + [Subject, referenced to the image] + [Ordered physical action] + [Camera path] + [Setting / weather reaction] + [Native audio] + [Identity lock] + [Exclusions]`

```text
Use the image as the first frame. Single continuous shot, no scene cuts. The young man in the image slides
across the wet steel on his belly, grabs the fish with his right hand, sweeps it to his chest with his left
arm, cannot stop, and drops sideways off the edge into the grey sea with a heavy splash. Real speed, no slow
motion. Low side angle tracking with him. Audio: clothes scraping on steel, a distant shout "Non !", the
splash. Keep every person's face, hair and clothing exactly as in the first frame. No music. No on-screen text.
```

### Omni-Specific Rules
- **Cuts are the default.** Omni builds several shots into a mini narrative unless you write `Single continuous shot, no scene cuts`. When you want cuts, script them with timecodes (`[0-5s] … [5-10s] …`) or natural timing (`After 3 seconds, cut to…`). Use at most 2–3 beats per 10 s.
- **Identity comes from the image, not the text.** Every shot that shows a recurring person must start from a keyframe that already contains that person. The pipeline is: character sheet → `generate-image(image_paths=[sheet(s), set])` keyframe → `generate-video(image_path=keyframe)`. If you animate from a set-only reference and describe the character in text, you get a different face every time.
- **Don't restate the face or age in the video prompt.** Refer to people by role (`the young man in the image`, `the young woman`, `the diver`) and add one identity lock sentence. Text that contradicts the frame overrides it: writing "mid-twenties" over a teen-looking keyframe aged the actor across shots. Never write ages under 18; leave age to the image.
- **Write physical effort for danger and drama.** Words like `serene` and `peaceful` turn peril into posed portraits, such as someone smiling underwater while holding a fish. Describe body mechanics instead: `kicks hard, straining upward, cheeks puffed, bubbles escaping`. Give a speed (`real speed, no slow motion`) and gravity (`the fish touches the deck between jumps`).
- **Describe geography positively and only once.** For example: `only the narrow summit of the tower rises above the sea; its base and arches are far beneath the water`. Also lock the setting to the image (`keep the set and the single tower exactly as in the image`). A long list of "no X, no Y" pulls the X and Y into frame.
- **Audio is generated by default.** Name the ambience and SFX you want. Write `No music` when you will score in post. Put dialogue in double quotes, name the language, and keep it to about 12 words per 10 s so lip-sync completes before the clip ends.
- **Keep exclusions short and at the end:** `No music. No on-screen text, no subtitles.` They are plain sentences, not a parameter.
- **Meta prompting helps detail.** For example: `Consider micro-detail, expression and timing for a rich but entirely natural scene.` Use it once and keep it short.

### Safety Rejections (`UPSTREAM_REJECTED`)
- Rejections come back fast (about 15–90 s) and spend no visible credits, but identical resubmits usually fail again. One 20-shot film logged 55 rejections from blind retries. Rephrase before you retry.
- These phrasings triggered rejections in image and video prompts: catastrophe framing (`swallowed`, `drowned city`, `survivors`), drowning, bodies or corpses, and ages of minors. Neutral rewrites passed: `a calm ocean where Paris used to be`, `residents`, `struggling to hold his breath`, `a torn coat snagged on a beam` (to imply loss).
- Do not pre-filter what the user asked for. Rephrase only after an actual rejection.

### Budget & Review Loop
- One 10 s clip used about 3.4k of the 48k credits in the Ultra 5-hour window on 2026-11-28, so plan about 12–14 clips per window; a reference-edited image used about 0.1k. Call `check-quota` before a batch.
- **Run video jobs one at a time.** With 3 submitted together, 2 failed as `RATE_LIMITED`, and the message blamed the `current_5h` quota even though `check-quota` still showed 36k credits. The same jobs passed when submitted one by one, about 100 s each. If `check-quota` shows credits left after a rate-limit error, reduce parallel submissions and verify the original operation before retrying. The exact cause may be unconfirmed. Images tolerate 3–4 parallel jobs.
- Submit with `wait_seconds=0` and poll each `operation_id`. Never resubmit a running handle.
- Review every clip yourself. Extract frames over the whole clip and compare the faces with the character sheet. Check physics, extra landmarks and stray text. Note trim points: the first and last ~0.5 s and any morph segment.
- `ask-gemini` can critique a single ≤15 s clip, but a 4-minute film proxy timed out. Review clip by clip.

---

## 3. Music & Song Generation (`music`)

Parameters: `duration` (`"short" | "standard"`), `vocals` (`"instrumental" | "vocals" | "auto"`), `genre` (18 presets or `"auto"`).

### Six-Component Music Formula

`[Genre & Era/Fusion] + [Mood & Emotion] + [Instrumentation] + [Tempo & Rhythm Feel] + [Vocal Style & Language] + [Dynamic Arc / Lyrics]`

1. **Genre & Fusion**: Style and era (`"1980s retro synth-pop with modern indie production"`, `"warm fingerstyle acoustic folk"`).
2. **Mood & Emotion**: Emotional tone (`"nostalgic yet uplifting"`, `"calm rainy-day coziness"`).
3. **Instrumentation**: Lead and backing instruments (`"warm muted felt piano, delicate folk harp plucks, gentle mellow cello harmonies"`).
4. **Tempo & Feel**: Use natural tempo/feel phrasing (`"relaxed unhurried waltz"`, `"upbeat driving groove around 118 BPM"`).
5. **Vocals / Language**: Specify voice character or lyrics when `vocals="vocals"`, or keep `vocals="instrumental"`.
6. **Dynamic Arc**: High-level flow (`"starts with soft solo felt piano, gradually swells with warm strings, ends gently"`).

### Important Music Tip (Avoiding Text-Only Blueprint Replies)
- **Keep prompts natural and musical**: Avoid asking for MIDI chord voicings, bar-by-bar DAW instructions (e.g., `"render Bars 1-32 with ostinato DSP"`), or technical audio-engineering deliverables — those trigger the language model to reply with a text blueprint instead of synthesizing audio.
- Always pair structured parameters (`duration`, `vocals`, `genre`) cleanly; Gemini Web enforces `low` thinking automatically on `music` to prevent text-only composer fallbacks.
