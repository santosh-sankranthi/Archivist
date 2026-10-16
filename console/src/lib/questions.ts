/** The pinned starter questions — one source for Home and the ⌘K palette,
 *  so the demo script and the UI can't drift apart. These are the questions
 *  the M2 demo pack rehearsed against the real `repo:flask` graph. */
export const STARTERS = [
  "What breaks if flask.helpers.url_for changes?",
  "Who should review a change to src/flask/app.py?",
  "Shortest call path from Flask.wsgi_app to Flask.finalize_request?",
] as const;
