// Mock sound library for SoundField prototype.
// Generated to feel like a real ~3TB game sound library.

const CATEGORIES = {
  "Footsteps": {
    sub: ["Wood", "Gravel", "Concrete", "Water", "Metal", "Grass", "Snow"],
    kind: "percussive", durRange: [0.3, 1.2]
  },
  "Combat": {
    sub: ["Sword", "Gun", "Punch", "Explosion", "Bow", "Shield"],
    kind: "transient", durRange: [0.4, 3.5]
  },
  "UI": {
    sub: ["Click", "Hover", "Confirm", "Error", "Notification", "Tab"],
    kind: "ui", durRange: [0.1, 0.8]
  },
  "Ambience": {
    sub: ["Forest", "City", "Rain", "Wind", "Ocean", "Cave", "Tavern"],
    kind: "ambient", durRange: [12, 60]
  },
  "Foley": {
    sub: ["Cloth", "Paper", "Door", "Bottle", "Chain", "Leather"],
    kind: "transient", durRange: [0.4, 2.0]
  },
  "Voice": {
    sub: ["Effort", "Breath", "Scream", "Pain", "Death", "Grunt"],
    kind: "voice", durRange: [0.5, 2.5]
  },
  "Music": {
    sub: ["Stinger", "Drone", "Percussion", "Loop"],
    kind: "musical", durRange: [3, 30]
  },
  "Mechanical": {
    sub: ["Engine", "Gears", "Click", "Hydraulic", "Servo"],
    kind: "mechanical", durRange: [1, 8]
  },
  "Magic": {
    sub: ["Whoosh", "Sparkle", "Charge", "Cast", "Impact"],
    kind: "magic", durRange: [0.8, 4]
  },
  "Creature": {
    sub: ["Roar", "Growl", "Movement", "Chirp"],
    kind: "voice", durRange: [0.6, 3]
  }
};

const DESCRIPTORS = {
  size: ["Small", "Light", "Med", "Heavy", "Huge", "Massive"],
  tone: ["Soft", "Warm", "Bright", "Dark", "Sharp", "Dull", "Crisp"],
  speed: ["Slow", "Fast", "Quick", "Slick"],
  feel: ["Dry", "Wet", "Reverb", "Tight", "Organic", "Mech"]
};

function hash(s) {
  let h = 0; for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function rand(seed) {
  // Mulberry32
  let s = seed | 0;
  return () => {
    s = (s + 0x6D2B79F5) | 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(arr, r) { return arr[Math.floor(r() * arr.length)]; }

// Synthesize peaks (channels × slices × [min,max]) for waveform rendering.
// Returns Float32Array flattened as well as segments [start_ratio, end_ratio][].
function generatePeaks(kind, durSec, channels, seed) {
  const r = rand(seed);
  const N = 512;
  const peaks = new Float32Array(N * channels * 2);
  const segments = [];
  let cur = 0;

  // Build a "hit list" of sound events based on kind
  let events = [];
  if (kind === "percussive") {
    // 3-6 footsteps spread evenly
    const n = 3 + Math.floor(r() * 4);
    for (let i = 0; i < n; i++) {
      const t = (i + 0.2 + r() * 0.4) / n;
      events.push({ pos: t, attack: 0.005, decay: 0.04 + r() * 0.06, amp: 0.6 + r() * 0.3 });
    }
  } else if (kind === "transient") {
    const n = 1 + Math.floor(r() * 2);
    for (let i = 0; i < n; i++) {
      events.push({ pos: 0.1 + (i / n) * 0.8 + r() * 0.05, attack: 0.005, decay: 0.15 + r() * 0.3, amp: 0.7 + r() * 0.3 });
    }
  } else if (kind === "ui") {
    events.push({ pos: 0.15, attack: 0.003, decay: 0.1, amp: 0.6 });
  } else if (kind === "ambient") {
    // diffuse continuous noise
    events.push({ pos: 0.5, attack: 0.3, decay: 0.7, amp: 0.25, sustain: true });
  } else if (kind === "voice") {
    const n = 1 + Math.floor(r() * 2);
    for (let i = 0; i < n; i++) {
      events.push({ pos: 0.2 + i * 0.4, attack: 0.05, decay: 0.4, amp: 0.65 + r() * 0.2, sustain: false });
    }
  } else if (kind === "musical") {
    events.push({ pos: 0.05, attack: 0.15, decay: 0.85, amp: 0.55, sustain: true });
  } else if (kind === "mechanical") {
    // looping cycles
    const n = 6 + Math.floor(r() * 8);
    for (let i = 0; i < n; i++) {
      events.push({ pos: i / n + r() * 0.02, attack: 0.01, decay: 0.05, amp: 0.4 + r() * 0.2 });
    }
  } else if (kind === "magic") {
    events.push({ pos: 0.1, attack: 0.2, decay: 0.5, amp: 0.5, sustain: true });
    events.push({ pos: 0.7, attack: 0.01, decay: 0.2, amp: 0.85 });
  }

  for (let c = 0; c < channels; c++) {
    for (let i = 0; i < N; i++) {
      const t = i / N;
      let amp = 0;
      for (const ev of events) {
        const dt = t - ev.pos;
        if (dt < 0) {
          if (dt > -ev.attack) {
            amp = Math.max(amp, ev.amp * (1 + dt / ev.attack));
          }
        } else {
          if (ev.sustain && dt < ev.decay) {
            amp = Math.max(amp, ev.amp * (0.7 + 0.3 * Math.sin(t * 80 + c)));
          } else if (dt < ev.decay) {
            amp = Math.max(amp, ev.amp * Math.exp(-dt / (ev.decay * 0.35)));
          }
        }
      }
      // texture: per-slice noise modulated by amp
      const noise = (r() - 0.5) * 2;
      const chOffset = c === 1 ? 0.85 + r() * 0.1 : 1.0;
      const val = amp * (0.7 + 0.3 * noise) * chOffset;
      const k = (i * channels + c) * 2;
      peaks[k] = -Math.abs(val);
      peaks[k + 1] = Math.abs(val);
    }
  }

  // Segments: derive from event positions for non-ambient
  if (kind === "ambient" || kind === "musical") {
    segments.push([0, 1]);
  } else if (events.length) {
    const cuts = [0];
    for (let i = 0; i < events.length; i++) {
      const nextPos = i + 1 < events.length ? events[i + 1].pos : 1.05;
      const gap = Math.min(nextPos - events[i].pos, events[i].decay + 0.05);
      const segEnd = Math.min(events[i].pos + gap + 0.02, 1);
      cuts.push(segEnd);
    }
    cuts[cuts.length - 1] = 1;
    for (let i = 0; i < cuts.length - 1; i++) {
      if (cuts[i + 1] - cuts[i] > 0.01) segments.push([cuts[i], cuts[i + 1]]);
    }
  } else {
    segments.push([0, 1]);
  }

  return { peaks, channels, segments };
}

function makeFile(cat, sub, idx) {
  const seed = hash(`${cat}/${sub}/${idx}`);
  const r = rand(seed);
  const def = CATEGORIES[cat];
  const tones = [pick(DESCRIPTORS.tone, r), pick(DESCRIPTORS.size, r)];
  if (r() > 0.6) tones.push(pick(DESCRIPTORS.feel, r));
  const baseName = `${sub}_${tones.join("_")}_${String(idx + 1).padStart(2, "0")}`;
  const channels = r() > 0.55 ? 2 : (r() > 0.85 ? 6 : 1);
  const sr = pick([44100, 48000, 48000, 48000, 96000], r);
  const bit = pick([16, 24, 24, 32], r);
  const dur = def.durRange[0] + r() * (def.durRange[1] - def.durRange[0]);
  const fmt = r() > 0.9 ? (r() > 0.5 ? "flac" : "mp3") : "wav";
  const sizeMb = +(dur * channels * sr * (bit / 8) / (1024 * 1024) * (fmt === "wav" ? 1 : 0.4)).toFixed(2);
  return {
    id: `f${seed}`,
    file_name: `${baseName}.${fmt}`,
    file_path: `Y:/[Library]/${cat}/${sub}/${baseName}.${fmt}`,
    folder: `Y:/[Library]/${cat}/${sub}`,
    category: cat,
    sub_category: sub,
    duration_sec: +dur.toFixed(2),
    sample_rate: sr,
    channels,
    bit_depth: bit,
    format: fmt,
    size_mb: sizeMb,
    kind: def.kind,
    title: baseName.replace(/_/g, " "),
    description: `${tones.join(", ")} ${sub.toLowerCase()} (${cat})`,
    keywords: [cat, sub, ...tones].join(", "),
    added_days_ago: Math.floor(r() * 365),
    seed
  };
}

const FILES = (() => {
  const out = [];
  for (const cat in CATEGORIES) {
    const def = CATEGORIES[cat];
    for (const sub of def.sub) {
      const n = 3 + Math.floor(rand(hash(cat + sub))() * 8);
      for (let i = 0; i < n; i++) out.push(makeFile(cat, sub, i));
    }
  }
  return out;
})();

// Folder tree built from file paths
function buildTree(files) {
  const root = { name: "Y:/[Library]", path: "Y:/[Library]", children: {}, count: 0 };
  for (const f of files) {
    const parts = f.file_path.split("/").slice(1, -1); // drop Y: and filename
    let node = root;
    let acc = "Y:";
    for (const part of parts) {
      acc += "/" + part;
      if (!node.children[part]) {
        node.children[part] = { name: part, path: acc, children: {}, count: 0 };
      }
      node = node.children[part];
      node.count++;
    }
    root.count++;
  }
  // Convert children map to sorted array recursively
  function freeze(n) {
    const kids = Object.values(n.children).sort((a, b) => a.name.localeCompare(b.name));
    return { name: n.name, path: n.path, count: n.count, children: kids.map(freeze) };
  }
  return freeze(root);
}

const TREE = buildTree(FILES);

window.SF_DATA = { FILES, TREE, CATEGORIES, generatePeaks };
