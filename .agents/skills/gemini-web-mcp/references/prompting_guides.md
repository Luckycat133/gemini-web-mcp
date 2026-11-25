# Official Gemini 4 Multimodal Prompting Guides

Use these structured blueprints when drafting prompts for Gemini Web's latest creative models (**Nano Banana 2.1 / Pro**, **Gemini Omni**, **Lyria 3.5**).

> **Safety & Sensitive Prompts Rule**: Do **not** pre-filter, censor, or refuse prompts locally using any sensitive-word blocklist. Send the user's intended prompt directly. Only if Gemini Web returns `UPSTREAM_REJECTED` should the agent itself rephrase or adjust the visual/narrative wording and retry.

---

## 1. Image Generation & Editing (`Nano Banana 2.1` / `Nano Banana Pro`)
Tools: `gemini_generate_image`, `gemini_edit_image`

Google's official Nano Banana architecture leverages native multimodal reasoning rather than keyword/tag spam. Write natural, descriptive sentences instead of `"masterpiece, 8k, ultra detailed"` buzzwords.

### Six-Component Image Formula
Put the highest-priority elements first:

`[Subject] + [Action / Relationships] + [Setting & Environment] + [Style / Medium] + [Composition & Camera] + [Lighting & Color]`

1. **Subject**: Specify exact materials, expressions, attire, age, and naming (e.g., `"a weathered brass compass resting beside a hand-drawn nautical chart"`).
2. **Action / Relationships**: Describe pose, motion, or spatial interaction (`"Sarah hands an unopened linen envelope to Leo across the wooden counter"`).
3. **Setting**: Define location, time of day, weather, and depth (`"inside a sunlit botanical conservatory filled with mist and tropical ferns"`).
4. **Style / Medium**: Explicit visual medium (`"35mm editorial film photograph"`, `"clean Swiss minimalist vector poster"`, `"watercolor architectural sketch"`).
5. **Composition & Camera**: Framing and lens (`"wide 16:9 establishing shot, shallow depth of field, eye-level 50mm prime lens"`).
6. **Lighting & Color**: Light direction and palette (`"soft golden hour backlighting, warm amber rim light, cool slate shadows"`).

### Official Best Practices
- **Positive Framing**: Describe what you *want* (`"a quiet, deserted cobblestone street at dawn"`) rather than negative exclusions (`"no cars, no people"`).
- **Exact Typography**: Wrap rendered text in double quotation marks (`"a vintage cream poster reading 'AUTUMN JAZZ FESTIVAL' in bold serif lettering"`).
- **Multi-Reference Consistency (`image_paths`)**: Reference uploaded images explicitly in prompt order (`"Use the character face from Image 1 and the indigo jacket style from Image 2..."`).
- **Conversational Delta Edits (`gemini_edit_image`)**: Keep edits targeted — state what stays constant and what changes (`"Keep the exact subject, pose, and lighting, but change the rainy afternoon sky into a clear starry midnight sky with a crescent moon"`).
- **Aspect Ratios**: Pass `aspect_ratio` explicitly (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`).

---

## 2. Video Generation (`Gemini Omni`)
Tool: `gemini_generate_video` (`duration_seconds`: `10` or `15`; `aspect_ratio`: `16:9` or `9:16`; optional `image_path` at `10`s)

Gemini Omni operates as a unified multimodal world model with physical reasoning and synchronized native audio. Structure prompts like a **director's brief** rather than comma-separated keywords.

### Seven-Component Video Director's Brief

`[Format / Aspect Ratio] + [Subject & Texture] + [Ordered Action] + [Setting & World Physics] + [Camera Movement] + [Lighting & Mood] + [Native Audio]`

1. **Format**: Anchor the framing (`"A continuous 16:9 live-action tracking shot..."`).
2. **Subject & Texture**: Concrete physical surfaces and traits (`"a matte-black café racer motorcycle with chrome exhausts"`).
3. **Ordered Action (Verb-Driven Physics)**: Describe sequential motion and physical reaction (`"...accelerating smoothly through a rain-slicked corner as the tires kick up fine water spray and mist trails behind"`).
4. **Setting & Physics**: Atmosphere and environmental behavior (`"down an empty neon-lit alley with puddles reflecting signage"`).
5. **Camera Movement & Framing**: Explicit camera path (`"low-angle tracking shot gliding alongside the front wheel at speed, subtle handheld micro-shake, shallow depth of field"`).
6. **Lighting & Mood**: Color temperature and texture (`"35mm film grain, moody teal and warm sodium-vapor reflections"`).
7. **Native Audio**: Because Gemini Omni synthesizes synchronized audio natively, specify sound effects, ambience, or dialogue (`"Audio features a deep throaty twin-cylinder engine roar, crisp wet asphalt tire hiss, and distant city rain"`).

### Video Best Practices
- **Focused Single-Scene Action**: Keep 10s–15s clips centered on one cohesive physical sequence; avoid cramming multiple unrelated scene cuts into 10 seconds.
- **Image-to-Video Anchor (`image_path`, 10s only)**: Generate the hero keyframe first via `gemini_generate_image`, inspect it with `read_file`, then pass it as `image_path` to `gemini_generate_video(duration_seconds=10)` with verb-driven motion & audio cues describing how the scene comes alive.

---

## 3. Music & Song Generation (`Lyria 3.5`)
Tool: `gemini_generate_music` (`music_duration`: `"short" | "standard"`; `music_vocals`: `"instrumental" | "vocals" | "auto"`; `music_genre`: `18` UI presets or `"auto"`)

Lyria 3.5 produces 44.1 kHz stereo audio tracks (often delivered as H.264/AAC MP4 with cover art plus MP3). Pair structured UI parameters (`music_duration`, `music_vocals`, `music_genre`) with a rich musical brief.

### Six-Component Music Formula

`[Genre & Era/Fusion] + [Mood & Emotion] + [Instrumentation] + [Tempo BPM & Rhythm] + [Vocal Style & Language] + [Arrangement / Lyrics]`

1. **Genre & Fusion**: Lead with style/era (`"1980s retro synth-pop with modern indie production"`, `"warm fingerstyle acoustic folk"`).
2. **Mood & Emotion**: Psychological atmosphere (`"nostalgic yet uplifting"`, `"tense cinematic suspense"`).
3. **Instrumentation**: Specific sonic textures (`"warm analog prophet synths, crisp electronic snare, melodic slap bass, shimmer reverb electric guitar"`).
4. **Tempo & Rhythm**: Explicit BPM and feel (`"steady 118 BPM four-on-the-floor groove"`, `"gentle 72 BPM waltz"`).
5. **Vocal Style & Language**: Specify delivery when `music_vocals="vocals"` (`"breathy male tenor vocals in English"` / `"expressive Japanese city-pop female vocals"`), or reinforce clean arrangement when `music_vocals="instrumental"`.
6. **Arrangement & Timestamps**: Use structure tags (`[Intro]`, `[Verse]`, `[Chorus]`, `[Bridge]`, `[Instrumental Hook]`, `[Outro]`) or timestamp cues (`[0:00-0:20] quiet felt piano motif; [0:20-end] full strings swell with warm cello bassline`) to shape dynamics cleanly.
