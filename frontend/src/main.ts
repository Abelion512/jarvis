/**
 * JARVIS — Main entry point.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createVoiceInput, createAudioPlayer, createMicMonitor, createPCMInput } from "./voice";
import { createSocket } from "./ws";
import { openSettings, checkFirstTimeSetup } from "./settings";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking" | "compacting";
let currentState: State = "idle";
let isMuted = false;

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;

function showError(msg: string) {
  errorEl.textContent = msg;
  errorEl.style.opacity = "1";
  setTimeout(() => {
    errorEl.style.opacity = "0";
  }, 5000);
}

function updateStatus(state: State) {
  const labels: Record<State, string> = {
    idle: "",
    listening: "listening...",
    thinking: "thinking...",
    speaking: "",
    compacting: "",          // the notice banner carries the words; the orb carries the state
  };
  statusEl.textContent = labels[state];
}

// ---------------------------------------------------------------------------
// Init components
// ---------------------------------------------------------------------------

const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
const orb = createOrb(canvas);

const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
const socket = createSocket(WS_URL);

const audioPlayer = createAudioPlayer();
orb.setAnalyser(audioPlayer.getAnalyser());

let muteMicDuringSpeech = false;

function transition(newState: State) {
  if (newState === currentState) return;
  currentState = newState;
  const btn = document.getElementById("hush");
  if (btn) (btn as HTMLButtonElement).hidden = newState !== "speaking";
  orb.setState(newState as OrbState);
  updateStatus(newState);

  if (isMuted) return;
  if (newState === "speaking" && muteMicDuringSpeech) {
    voiceInput.pause();
  } else {
    voiceInput.resume();
  }
}

// ---------------------------------------------------------------------------
// Voice input
// ---------------------------------------------------------------------------

// Spoken-language selector: EN → ID → ZH. Decides both the recogniser tag
// (en-US/id-ID/zh-CN) and the voice the reply is synthesised with.
const SPOKEN_TAGS = ["en-US", "id-ID", "zh-CN"] as const;
const SPOKEN_CODES = ["EN", "ID", "ZH"] as const;
const storedLang = localStorage.getItem("jarvis-lang");
let spokenIdx = Math.max(0, SPOKEN_CODES.indexOf(
  storedLang === "ID" || storedLang === "ZH" ? storedLang : "EN"));
let spokenLang: "en" | "id" | "zh" = SPOKEN_CODES[spokenIdx].toLowerCase() as "en" | "id" | "zh";

const voiceInput = createVoiceInput(
  (text: string) => {
    // The server decides whether this is echo, a barge-in, or a new turn.
    micMonitor.sawSpeech();
    socket.send({ type: "transcript", text, isFinal: true, lang: spokenLang });
  },
  (text: string) => {
    micMonitor.sawSpeech();
    socket.send({ type: "interim", text, lang: spokenLang });
  },
  (msg: string) => {
    showError(msg);
  },
  (event: string) => {
    // Mirror the recogniser's lifecycle to the server log. Going deaf is a
    // browser-side failure the server cannot otherwise see at all, and the
    // console it used to be confined to is never open when it happens.
    socket.send({ type: "mic", text: event });
  }
);

// A live meter for the microphone itself. If this moves when you speak, the
// microphone is working — whatever else is or is not happening. It answers
// "is it even hearing me?" without a log, a console or anyone to ask.
const micDot = document.createElement("div");
micDot.id = "mic-level";
micDot.title = "microphone input";
document.body.appendChild(micDot);

const micMonitor = createMicMonitor(
  (level: number) => {
    const pct = Math.min(100, Math.round(level * 900));
    micDot.style.setProperty("--level", `${pct}%`);
    micDot.classList.toggle("is-hot", level > 0.02);
  },
  (event: string) => {
    socket.send({ type: "mic", text: event });
    // Proven deaf: sound going in, nothing coming out. Do not wait for the
    // rotation timer to happen along — measured once at 21 seconds, all of
    // it lost. Rebuild the recogniser now.
    if (event.startsWith("DEAF")) voiceInput.restart("deaf: audio in, no results");
  }
);

// ── stopping him ──────────────────────────────────────────────────────────
// Escape, or the button that appears while he is talking. Not a spoken word:
// his voice comes back through the microphone garbled, and a mis-hear that
// looked like "stop" would cut him off at random. A keystroke cannot be
// misheard.
const hushBtn = document.createElement("button");
hushBtn.id = "hush";
hushBtn.type = "button";
hushBtn.textContent = "Stop";
hushBtn.title = "Stop speaking (Esc)";
hushBtn.hidden = true;
document.body.appendChild(hushBtn);

function hush() {
  if (currentState !== "speaking") return;
  // Locally first: the round trip is real and silence should be instant.
  audioPlayer.stop();
  socket.send({ type: "hush" });
  transition(isMuted ? "idle" : "listening");
}

hushBtn.addEventListener("click", hush);
window.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key === "Escape") { e.preventDefault(); hush(); }
  // 1/2/3 switches recognition language without reaching for the mouse.
  // Ignored while typing in a field, so setting your name never flips it.
  const target = document.activeElement;
  const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  if (!typing && (e.key === "1" || e.key === "2" || e.key === "3")) {
    setSpokenLang(Number(e.key) - 1);
  }
});

audioPlayer.onPlayed((utt, idx) => {
  socket.send({ type: "played", utt, idx });
});

// End of speech is the server's call (`status: idle` after every chunk is
// acked); a transient empty queue mid-utterance must not flip the UI.
audioPlayer.onFinished(() => {});

audioPlayer.onNeedsGesture(() => {
  showError("Click anywhere to enable audio");
});

// ---------------------------------------------------------------------------
// WebSocket messages
// ---------------------------------------------------------------------------

socket.onMessage((msg) => {
  const type = msg.type as string;

  if (type === "config") {
    muteMicDuringSpeech = Boolean(msg.muteMicDuringSpeech);
  } else if (type === "audio") {
    const data = msg.data as string;
    if (data) {
      if (currentState !== "speaking") transition("speaking");
      audioPlayer.enqueue(data, Number(msg.utt), Number(msg.idx));
    }
    if (msg.text) console.log("[JARVIS]", msg.text);
  } else if (type === "stop") {
    audioPlayer.stop();
    transition(isMuted ? "idle" : "listening");
  } else if (type === "drop_queued") {
    audioPlayer.dropQueued();
  } else if (type === "status") {
    const state = msg.state as string;
    if (state === "thinking") transition("thinking");
    else if (state === "speaking") transition("speaking");
    else if (state === "compacting") transition("compacting");
    else if (state === "idle") transition(isMuted ? "idle" : "listening");
  } else if (type === "text") {
    // A chunk TTS could not voice: show it instead of losing it
    console.log("[JARVIS]", msg.text);
    statusEl.textContent = String(msg.text);
  } else if (type === "live_audio") {
    // Gemini Live reply: base64 16-bit PCM 24kHz mono → speaker.
    const b64 = String(msg.data ?? "");
    if (b64) {
      try {
        const bin = atob(b64);
        const pcm = new Int16Array(bin.length / 2);
        for (let i = 0; i < pcm.length; i++) {
          pcm[i] = (bin.charCodeAt(i * 2) & 0xff) | (bin.charCodeAt(i * 2 + 1) << 8);
        }
        if (currentState !== "speaking") transition("speaking");
        audioPlayer.playPCM(pcm);
      } catch (err) {
        console.error("[audio] live chunk unplayable:", err);
      }
    }
  } else if (type === "notice") {
    // Shown, never spoken. The server sends one when it is about to be busy
    // for a few seconds (a context rotation), and an empty string to clear it.
    // Without it the pause looks like a crash.
    const text = String(msg.text ?? "");
    statusEl.textContent = text;
    if (text) console.log("[notice]", text);
  }
});

// ---------------------------------------------------------------------------
// Kick off
// ---------------------------------------------------------------------------

// Start listening after a brief delay for the orb to render
setTimeout(() => {
  voiceInput.start();
  if (currentState !== "speaking") transition("listening");
  // Live-session PCM path, opt-in only: ?stt=gemini streams mic PCM over the
  // same socket (server routes bytes to Gemini when JARVIS_STT_PROVIDER=gemini).
  // Default path unchanged.
  try {
    if (new URLSearchParams(window.location.search).get("stt") === "gemini") {
      const pcm = createPCMInput((chunk) => socket.sendBytes(chunk));
      pcm.start().catch((e) => showError(`Live mic failed: ${e}`));
    }
  } catch { /* query parse never blocks voice */ }
}, 1000);

// Resume AudioContext on ANY user interaction (browser autoplay policy)
function ensureAudioContext() {
  const ctx = audioPlayer.getAnalyser().context as AudioContext;
  if (ctx.state === "suspended") {
    ctx.resume().then(() => console.log("[audio] context resumed"));
  }
}
document.addEventListener("click", ensureAudioContext);
document.addEventListener("touchstart", ensureAudioContext);
document.addEventListener("keydown", ensureAudioContext, { once: true });

// Try to resume audio context on load
ensureAudioContext();

// ---------------------------------------------------------------------------
// UI Controls
// ---------------------------------------------------------------------------

const btnMute = document.getElementById("btn-mute")!;
const btnMenu = document.getElementById("btn-menu")!;
const menuDropdown = document.getElementById("menu-dropdown")!;
const btnRestart = document.getElementById("btn-restart")!;
const btnFixSelf = document.getElementById("btn-fix-self")!;

btnMute.addEventListener("click", (e) => {
  e.stopPropagation();
  isMuted = !isMuted;
  btnMute.classList.toggle("muted", isMuted);
  if (isMuted) {
    voiceInput.pause();
    transition("idle");
  } else {
    voiceInput.resume();
    transition("listening");
  }
});

btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = menuDropdown.style.display === "none" ? "block" : "none";
});

// Spoken-language button: EN → ID → ZH. Applies to the recogniser (next
// rotation) and tags every transcript so the reply is synthesised with the
// matching Edge voice. Persisted across reloads.
const btnLang = document.createElement("button");
btnLang.id = "btn-lang";
btnLang.type = "button";
btnLang.title = "Recognition language (EN/ID/ZH)";
btnLang.textContent = SPOKEN_CODES[spokenIdx];
document.body.appendChild(btnLang);
voiceInput.setLanguage(SPOKEN_TAGS[spokenIdx]);
function setSpokenLang(idx: number) {
  spokenIdx = ((idx % SPOKEN_CODES.length) + SPOKEN_CODES.length) % SPOKEN_CODES.length;
  spokenLang = (["en", "id", "zh"] as const)[spokenIdx];
  localStorage.setItem("jarvis-lang", SPOKEN_CODES[spokenIdx]);
  btnLang.textContent = SPOKEN_CODES[spokenIdx];
  voiceInput.setLanguage(SPOKEN_TAGS[spokenIdx]);
}
btnLang.addEventListener("click", (e) => {
  e.stopPropagation();
  setSpokenLang(spokenIdx + 1);
});

document.addEventListener("click", () => {
  menuDropdown.style.display = "none";
});

btnRestart.addEventListener("click", async (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  statusEl.textContent = "restarting...";
  try {
    await fetch("/api/restart", { method: "POST" });
    // Wait a few seconds then reload
    setTimeout(() => window.location.reload(), 4000);
  } catch {
    statusEl.textContent = "restart failed";
  }
});

btnFixSelf.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  // Activate work mode on the WebSocket session (JARVIS becomes Claude Code's voice)
  // Milestone 1 has no tools yet; "Fix yourself" returns as a brain tool later.
  statusEl.textContent = "fix-yourself is not available in this build";
});

// Settings button
const btnSettings = document.getElementById("btn-settings")!;
btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  openSettings();
});

// First-time setup detection — check after a short delay for server readiness
setTimeout(() => {
  checkFirstTimeSetup();
}, 2000);
