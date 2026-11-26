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

---

## 2. Video Generation (`video`)

Parameters: `duration_seconds` (`10` or `15`), `aspect_ratio` (`16:9` or `9:16`), optional first-frame `image_path` (`10`s only).

Structure prompts as a **director's brief** combining camera motion, physical action, and synchronized native audio:

`[Format / Aspect Ratio] + [Subject & Texture] + [Ordered Action] + [Setting & World Physics] + [Camera Movement] + [Lighting & Mood] + [Native Audio]`

1. **Format**: Anchor the framing (`"A continuous 16:9 live-action tracking shot..."`).
2. **Subject & Texture**: Concrete physical surfaces (`"a matte-black cafe racer motorcycle with chrome exhausts"`).
3. **Ordered Action**: Verb-driven physics and environment reaction (`"...accelerating smoothly through a rain-slicked corner as tires kick up fine spray"`).
4. **Setting & Physics**: Atmosphere and environmental behavior (`"down an empty neon-lit alley with puddles reflecting signage"`).
5. **Camera Movement**: Explicit camera path (`"low-angle tracking shot gliding alongside the front wheel, subtle handheld micro-shake, shallow depth of field"`).
6. **Lighting & Mood**: Color temperature and grain (`"35mm film grain, moody teal and warm sodium-vapor reflections"`).
7. **Native Audio**: Sound effects, ambience, or dialogue (`"Audio features a deep twin-cylinder engine roar, wet asphalt tire hiss, and distant city rain"`).

### Best Practices
- **Focused Single-Scene Action**: Keep 10s–15s clips centered on one cohesive physical sequence; avoid packing unrelated cuts.
- **Keyframe Anchoring (`image_path`, 10s only)**: Generate a keyframe first with `image`, inspect it via `read_file`, then animate it with `video(duration_seconds=10, image_path=...)`.

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
