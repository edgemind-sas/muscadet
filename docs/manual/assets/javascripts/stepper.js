// Step controls for the animated figures drawn by docs/tools/figures.py.
//
// A figure carries data-steps (its last step). Every element inside it with
// data-from / data-until is shown while the current step lies in that range.
// When a step starts, the tokens appearing at that step ride their arrow.
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function inRange(el, step) {
    var from = Number(el.getAttribute("data-from"));
    var until = el.hasAttribute("data-until")
      ? Number(el.getAttribute("data-until"))
      : Infinity;
    return step >= from && step <= until;
  }

  function show(fig, step) {
    fig.setAttribute("data-current", step);
    fig.querySelectorAll("[data-from]").forEach(function (el) {
      var on = inRange(el, step);
      el.classList.toggle("mu-on", on);
      if (on && Number(el.getAttribute("data-from")) === step) {
        el.querySelectorAll("animateMotion").forEach(function (anim) {
          if (reduced) anim.setAttribute("dur", "0.01s");
          try {
            anim.beginElement();
          } catch (e) {
            /* SMIL unavailable: the token stays at its start. */
          }
        });
      }
    });
    var last = Number(fig.getAttribute("data-steps"));
    fig.querySelector(".mu-count").textContent =
      step === 0 ? "Setup" : "Step " + step + " / " + last;
    fig.querySelector(".mu-prev").disabled = step === 0;
    fig.querySelector(".mu-next").disabled = step === last;
  }

  function button(label, klass, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "md-button " + klass;
    b.textContent = label;
    b.title = title;
    return b;
  }

  function setup(fig) {
    if (fig.dataset.ready) return;
    fig.dataset.ready = "1";
    var last = Number(fig.getAttribute("data-steps"));
    var bar = document.createElement("div");
    bar.className = "mu-controls";
    if (last > 0) {
      var restart = button("⏮", "mu-restart", "Restart");
      var prev = button("◀", "mu-prev", "Previous step");
      var next = button("▶", "mu-next", "Next step");
      var play = button("Play", "mu-play", "Play every step");
      var count = document.createElement("span");
      count.className = "mu-count";
      var timer = null;
      var stop = function () {
        if (timer) clearInterval(timer);
        timer = null;
        play.textContent = "Play";
      };
      var current = function () {
        return Number(fig.getAttribute("data-current"));
      };
      restart.onclick = function () { stop(); show(fig, 0); };
      prev.onclick = function () { stop(); show(fig, Math.max(0, current() - 1)); };
      next.onclick = function () { stop(); show(fig, Math.min(last, current() + 1)); };
      play.onclick = function () {
        if (timer) return stop();
        if (current() === last) show(fig, 0);
        play.textContent = "Pause";
        timer = setInterval(function () {
          if (current() >= last) return stop();
          show(fig, current() + 1);
        }, 2600);
      };
      [restart, prev, next, play, count].forEach(function (el) {
        bar.appendChild(el);
      });
      fig.appendChild(bar);
      show(fig, 0);
    } else {
      fig.querySelectorAll("[data-from]").forEach(function (el) {
        el.classList.add("mu-on");
      });
    }
  }

  function init() {
    document.querySelectorAll("figure.mu-figure").forEach(setup);
  }

  if (window.document$ && window.document$.subscribe) {
    window.document$.subscribe(init);
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
