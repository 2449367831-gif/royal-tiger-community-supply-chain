(() => {
  'use strict';
  const nav = document.querySelector('.side-nav');
  const links = [...nav.querySelectorAll('.side-link')];
  const capsule = document.createElement('span');
  capsule.className = 'nav-capsule';
  capsule.setAttribute('aria-hidden', 'true');
  nav.prepend(capsule);
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  let animation = null;
  let keyboardNavigation = false;

  // Exact underdamped response: mass 1, stiffness 100, damping 10.
  // Sample the physical response into compositor-only WAAPI transform frames.
  const omega = Math.sqrt(100 - 5 * 5);
  const response = t => 1 - Math.exp(-5 * t) * (Math.cos(omega * t) + 5 / omega * Math.sin(omega * t));
  const velocity = t => Math.exp(-5 * t) * 100 / omega * Math.sin(omega * t);
  const physicalEnd = 1.6;
  const finalResponse = response(physicalEnd);

  function moveTo(link, animated) {
    const navRect = nav.getBoundingClientRect();
    const targetRect = link.getBoundingClientRect();
    if (!targetRect.height || !navRect.width) return;
    const targetY = targetRect.top - navRect.top;
    const current = new DOMMatrixReadOnly(getComputedStyle(capsule).transform);
    const fromY = current.m42;
    const fromScale = current.m22;
    if (animation) {
      // Freeze the currently painted transform before cancelling a previous move.
      capsule.style.transform = current.toString();
      animation.cancel();
      animation = null;
    }
    capsule.style.left = `${targetRect.left - navRect.left}px`;
    capsule.style.width = `${targetRect.width}px`;
    capsule.style.height = `${targetRect.height}px`;
    capsule.style.opacity = '1';
    const finalTransform = `translateY(${targetY}px) scaleY(1)`;
    if (!animated || reduce.matches || Math.abs(targetY - fromY) < .5) {
      capsule.style.transform = finalTransform;
      return;
    }
    const distance = targetY - fromY;
    const direction = Math.sign(distance);
    const frames = Array.from({length: 61}, (_, index) => {
      const offset = index / 60;
      const t = offset * physicalEnd;
      const progress = index === 60 ? 1 : response(t) / finalResponse;
      // A moving front edge stretches toward the target; its returning edge compresses.
      const stretch = index === 60 ? 0 : Math.max(-.08, Math.min(.38,
        velocity(t) * Math.abs(distance) / targetRect.height * .04));
      const scale = 1 + (fromScale - 1) * (1 - progress) + stretch;
      const y = fromY + distance * progress - (direction < 0 ? stretch * targetRect.height : 0);
      return {offset, transform: `translateY(${y}px) scaleY(${scale})`};
    });
    const next = capsule.animate(frames, {duration: 500, easing: 'linear', fill: 'both'});
    animation = next;
    next.onfinish = () => {
      if (animation !== next) return;
      capsule.style.transform = finalTransform;
      next.cancel();
      animation = null;
    };
  }

  for (const link of links) {
    link.addEventListener('click', event => {
      if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey)
        keyboardNavigation = event.detail === 0;
    });
  }
  const activeLink = () => links.find(link => link.getAttribute('href') === location.hash)
    || nav.querySelector('.is-active') || links[0];
  const snap = () => moveTo(activeLink(), false);
  window.addEventListener('hashchange', () => {
    moveTo(activeLink(), !keyboardNavigation);
    keyboardNavigation = false;
  });
  new ResizeObserver(snap).observe(nav);
  reduce.addEventListener('change', snap);
  snap();
})();
