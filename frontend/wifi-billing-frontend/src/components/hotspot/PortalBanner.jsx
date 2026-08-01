import { useState } from "react";

/**
 * The advertising slot at the top of the captive portal.
 *
 * Two rules shape this component.
 *
 * It must never delay the packages. A portal visitor has no internet yet —
 * their only working route is to the walled garden — so an image is the
 * slowest thing on the page by a wide margin. It is lazy, async-decoded, and
 * rendered outside the data path entirely: packages never wait on it, and a
 * banner that fails to load leaves no gap and no broken frame.
 *
 * And it must not shift the page. The slot reserves its aspect ratio before
 * the image arrives, so the buttons underneath do not jump out from under a
 * thumb mid-tap. That is why the portrait and landscape sources are separate
 * images rather than one stretched to both: their ratios differ, and a
 * reserved box can only reserve one.
 */
export default function PortalBanner({ banner, provider }) {
  const [failed, setFailed] = useState(false);

  const portrait = banner?.portrait || null;
  const landscape = banner?.landscape || null;
  const link = banner?.link || null;

  // Nothing configured, or every source failed: fall back to the platform's
  // own panel, which is markup and costs nothing to render.
  if ((!portrait && !landscape) || failed) {
    return <HousePanel provider={provider} />;
  }

  const media = (
    <picture>
      {landscape && (
        <source media="(min-aspect-ratio: 1/1)" srcSet={landscape} />
      )}
      {portrait && (
        <source media="(max-aspect-ratio: 1/1)" srcSet={portrait} />
      )}
      <img
        src={landscape || portrait}
        alt={`Offer from ${provider || "your provider"}`}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
      />
    </picture>
  );

  return (
    <div
      className="mb-4 overflow-hidden rounded-2xl bg-slate-200 shadow-sm
                 aspect-[16/7] sm:aspect-[21/7] portrait:aspect-[3/2] sm:portrait:aspect-[16/7]"
    >
      {link ? (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="block h-full w-full"
        >
          {media}
        </a>
      ) : (
        media
      )}
    </div>
  );
}

/**
 * Shown when an operator has not set a banner, which is most of them on the
 * day they start. An empty slot at the top of the page reads as a failure, so
 * it says something true instead.
 */
function HousePanel({ provider }) {
  return (
    <div className="mb-4 overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900 px-5 py-6 text-center shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-sky-300/80">
        Welcome to
      </p>
      <p className="mt-1 truncate text-xl font-bold text-white">
        {provider || "our WiFi"}
      </p>
      <p className="mx-auto mt-2 max-w-xs text-xs leading-relaxed text-slate-300">
        Pick a package below and pay with M-Pesa. You'll be online in under a
        minute.
      </p>
    </div>
  );
}
