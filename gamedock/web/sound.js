// GameDock — sons da interface, sintetizados na hora com Web Audio (nenhum arquivo de áudio).
// Tudo passa por um "master" (volume geral) e um reverb curto que dá a sensação de espaço.

const Sfx = (() => {
  let ctx, master, reverb;
  let enabled = true;
  let volume = 0.6;

  function init() {
    ctx = new AudioContext();
    master = ctx.createGain();
    master.gain.value = volume;
    master.connect(ctx.destination);
    // reverb: ruído com decaimento exponencial como resposta ao impulso
    reverb = ctx.createConvolver();
    reverb.buffer = impulse(1.8, 3.5);
    const wet = ctx.createGain();
    wet.gain.value = 0.25;
    reverb.connect(wet).connect(master);
  }

  function impulse(seconds, decay) {
    const length = ctx.sampleRate * seconds;
    const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const data = buffer.getChannelData(ch);
      for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** decay;
    }
    return buffer;
  }

  function envelope(gain, t, attack, dur, peak) {
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(peak, t + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  }

  function route(node, wet) {
    node.connect(master);
    if (wet) node.connect(reverb);
  }

  // uma nota: oscilador + envelope (ataque rápido, cauda exponencial)
  function tone({ freq, type = "sine", start = 0, dur = 0.15, gain = 0.2, attack = 0.004, glideTo, wet = true, lowpass }) {
    const t = ctx.currentTime + start;
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    if (glideTo) osc.frequency.exponentialRampToValueAtTime(glideTo, t + dur);
    const g = ctx.createGain();
    envelope(g, t, attack, dur, gain);
    let last = osc.connect(g);
    if (lowpass) {
      const f = ctx.createBiquadFilter();
      f.type = "lowpass";
      f.frequency.value = lowpass;
      last = g.connect(f);
    }
    route(last, wet);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  // "sopro": ruído branco num filtro passa-banda que varre de `from` até `to` Hz
  function whoosh({ start = 0, dur = 0.3, gain = 0.1, from = 400, to = 4000, q = 1.4, wet = true }) {
    const t = ctx.currentTime + start;
    const buffer = ctx.createBuffer(1, ctx.sampleRate * dur, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = q;
    filter.frequency.setValueAtTime(from, t);
    filter.frequency.exponentialRampToValueAtTime(to, t + dur);
    const g = ctx.createGain();
    envelope(g, t, dur * 0.35, dur, gain);
    route(src.connect(filter).connect(g), wet);
    src.start(t);
  }

  const cents = (c) => 2 ** (c / 1200);

  const sounds = {
    move() {
      const wobble = cents((Math.random() - 0.5) * 30);  // ±15 cents: vivo, mas consistente
      tone({ freq: 1480 * wobble, type: "triangle", dur: 0.055, gain: 0.05, wet: false, lowpass: 4000 });
      tone({ freq: 740 * wobble, dur: 0.07, gain: 0.05, wet: false });
    },
    edge() {
      tone({ freq: 180, glideTo: 140, dur: 0.12, gain: 0.12, wet: false, lowpass: 900 });
    },
    play() {
      [523.25, 783.99, 1046.5].forEach((f, i) => tone({ freq: f, start: i * 0.07, dur: 1.1, gain: 0.09, attack: 0.01 }));
      tone({ freq: 2093, type: "triangle", start: 0.22, dur: 0.7, gain: 0.025 });
      whoosh({ start: 0.05, dur: 1.2, gain: 0.06, from: 250, to: 7000 });
      tone({ freq: 98, glideTo: 49, start: 0.12, dur: 1.0, gain: 0.2, attack: 0.03, wet: false });
    },
    favOn() {
      [1046.5, 1318.51, 1567.98].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.055, dur: 0.4, gain: 0.06 }));
    },
    favOff() {
      [880, 659.25].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.07, dur: 0.3, gain: 0.05 }));
    },
    open() {
      whoosh({ dur: 0.3, gain: 0.05, from: 500, to: 3200 });
      tone({ freq: 587.33, glideTo: 880, dur: 0.25, gain: 0.04 });
    },
    close() {
      whoosh({ dur: 0.24, gain: 0.04, from: 3200, to: 500 });
      tone({ freq: 880, glideTo: 587.33, dur: 0.2, gain: 0.035 });
    },
    success() {
      [659.25, 830.61, 987.77].forEach((f, i) => tone({ freq: f, start: i * 0.045, dur: 0.6, gain: 0.05 }));
    },
    error() {
      [233.08, 196].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.11, dur: 0.18, gain: 0.1, wet: false, lowpass: 1200 }));
    },
    // troféus: quanto mais raro, mais notas e mais brilho
    trophy_bronze() {
      [783.99, 1174.66].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.09, dur: 0.7, gain: 0.08 }));
    },
    trophy_silver() {
      [783.99, 987.77, 1318.51].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.08, dur: 0.8, gain: 0.08 }));
      tone({ freq: 2637.02, start: 0.26, dur: 0.6, gain: 0.02 });
    },
    trophy_gold() {
      [659.25, 830.61, 987.77, 1318.51].forEach((f, i) => tone({ freq: f, start: i * 0.075, dur: 1.1, gain: 0.08, attack: 0.008 }));
      [2637.02, 3135.96, 3951.07].forEach((f, i) => tone({ freq: f, type: "triangle", start: 0.32 + i * 0.06, dur: 0.7, gain: 0.018 }));
      whoosh({ start: 0.05, dur: 0.9, gain: 0.03, from: 2000, to: 9000 });
    },
    platinum() {
      [261.63, 329.63, 392, 523.25, 659.25, 783.99, 1046.5].forEach((f, i) =>
        tone({ freq: f, start: i * 0.085, dur: 2.2, gain: 0.06, attack: 0.02, lowpass: 5000 }));
      [2093, 2637.02, 3135.96, 4186.01].forEach((f, i) => tone({ freq: f, type: "triangle", start: 0.7 + i * 0.07, dur: 1.2, gain: 0.02 }));
      whoosh({ start: 0.1, dur: 2.0, gain: 0.04, from: 300, to: 8000 });
      tone({ freq: 65.41, start: 0.6, dur: 1.8, gain: 0.16, attack: 0.05, wet: false });
    },
    // obturador: dois cliques de ruído bem curtos + um "plim" agudo
    shutter() {
      whoosh({ dur: 0.04, gain: 0.12, from: 3000, to: 6000, q: 0.8, wet: false });
      whoosh({ start: 0.07, dur: 0.05, gain: 0.09, from: 5000, to: 2500, q: 0.8, wet: false });
      tone({ freq: 2349.32, type: "triangle", start: 0.12, dur: 0.35, gain: 0.025 });
    },
    // subiu de nível: arpejo subindo uma oitava inteira, terminando num acorde aberto
    levelup() {
      [392, 523.25, 659.25, 783.99, 1046.5].forEach((f, i) => tone({ freq: f, type: "triangle", start: i * 0.07, dur: 0.5, gain: 0.07 }));
      [523.25, 659.25, 783.99, 1046.5].forEach((f) => tone({ freq: f, start: 0.38, dur: 1.4, gain: 0.04, attack: 0.03 }));
      whoosh({ start: 0.3, dur: 1.0, gain: 0.03, from: 1500, to: 8000 });
    },
    // assinatura do GameDock: Dó maior com sétima abrindo devagar + brilho por cima
    boot() {
      [130.81, 196, 261.63, 329.63, 493.88].forEach((f, i) =>
        tone({ freq: f, type: i < 2 ? "sine" : "triangle", start: i * 0.07, dur: 2.4, gain: 0.055, attack: 0.35, lowpass: 2400 }));
      [1567.98, 2093, 2637.02].forEach((f, i) => tone({ freq: f, start: 0.55 + i * 0.09, dur: 1.0, gain: 0.02 }));
      whoosh({ dur: 1.6, gain: 0.025, from: 200, to: 2600 });
    },
  };

  return {
    play(name) {
      if (!enabled || !sounds[name]) return;
      try {
        if (!ctx) init();
        if (ctx.state === "suspended") ctx.resume();
        sounds[name]();
      } catch (err) {
        console.warn("som falhou", name, err);
      }
    },
    configure({ enabled: on, volume: vol }) {
      if (on !== undefined) enabled = on;
      if (vol !== undefined) {
        volume = vol;
        if (master) master.gain.setTargetAtTime(vol, ctx.currentTime, 0.05);
      }
    },
  };
})();
