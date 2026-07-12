# Music folder

Drop **royalty-free** audio tracks here (`.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac`).
LeagueClipFarm picks one at random per clip and mixes it quietly under the game audio.

- Leave this folder empty to skip music (clips still get vertical + normalization + fades).
- Add several tracks so your Shorts don't all use the same song.

## Where to get free, monetization-safe music

- **YouTube Audio Library** — youtube.com/audiolibrary (in YouTube Studio). Filter to
  "No attribution required". This is the safest source for videos you'll post *to* YouTube.
- **Pixabay Music** — pixabay.com/music (free, no attribution).
- **Incompetech (Kevin MacLeod)** — CC-BY (requires crediting the artist in your description).

Tracks are read from `editing.music_dir` in `config.yaml`. Volume, fades, and whether editing
runs at all are configurable there too.
