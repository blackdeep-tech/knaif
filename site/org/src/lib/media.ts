// The narrated explainer video, hosted on YouTube. knaif.org shows a still of it and loads
// YouTube's player only when the visitor presses play, so nothing is fetched from YouTube before
// that (see /privacy/). Without JS the still is a plain link to the video. With no id, nothing
// renders.
export const explainer = {
  youtubeId: "AZ1lGlUI9j8",
  /** Poster under public/demo/: the video's own opening frame. */
  poster: "/demo/explainer.jpg",
  duration: "2:20",
  title: "knaif in two minutes",
  blurb:
    "A narrated tour: a video resized, a GIF made, a request in Spanish, a clip fetched from a link, two PDFs merged, and a delete that is refused.",
};

export const explainerUrl = explainer.youtubeId ? `https://youtu.be/${explainer.youtubeId}` : "";
