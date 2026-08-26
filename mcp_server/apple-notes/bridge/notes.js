// JXA (JavaScript for Automation) driver for Apple Notes.
//
// Invoked as:  osascript -l JavaScript notes.js <action> <json-params>
// Returns:     JSON on stdout — {ok: true, data: ...} or {ok: false, error: "..."}.
//
// JXA is picked over pure AppleScript because JSON.stringify handles all the
// quoting/newline escaping that would otherwise be manual and fragile.

function run(argv) {
  const action = argv[0];
  const params = argv[1] ? JSON.parse(argv[1]) : {};
  const Notes = Application('Notes');

  const dispatch = {
    list_notes: listNotes,
    get_note: getNote,
    create_note: createNote,
    update_note: updateNote,
    delete_note: deleteNote,
    list_folders: listFolders,
    search_notes: searchNotes,
    ping: () => ({pong: true}),
  };

  const handler = dispatch[action];
  if (!handler) {
    return JSON.stringify({ok: false, error: `unknown action: ${action}`});
  }

  try {
    return JSON.stringify({ok: true, data: handler(Notes, params)});
  } catch (e) {
    return JSON.stringify({ok: false, error: e.message || String(e)});
  }
}

function safeFolder(note) {
  try { return note.container().name(); } catch (_) { return null; }
}

function isoDate(d) {
  try { return d.toISOString(); } catch (_) { return null; }
}

function summary(note) {
  return {
    id: note.id(),
    name: note.name(),
    folder: safeFolder(note),
    modified: isoDate(note.modificationDate()),
    created: isoDate(note.creationDate()),
  };
}

function full(note) {
  const s = summary(note);
  s.body = note.body();
  // .plaintext is only present on macOS 10.11+; guard just in case.
  try { s.plaintext = note.plaintext(); } catch (_) { s.plaintext = null; }
  return s;
}

// .byId() sometimes returns a stale reference on Notes; the whose-filter is
// slower but reliable across the coredata URL id format.
function findNoteById(Notes, id) {
  if (!id) throw new Error('id is required');
  const matches = Notes.notes.whose({id: id})();
  if (matches.length === 0) throw new Error(`note not found: ${id}`);
  return matches[0];
}

function findFolderByName(Notes, name) {
  const matches = Notes.folders.whose({name: name})();
  if (matches.length === 0) throw new Error(`folder not found: ${name}`);
  return matches[0];
}

function listNotes(Notes, {folder, limit}) {
  const source = folder ? findFolderByName(Notes, folder).notes() : Notes.notes();
  const out = source.map(summary);
  return (limit && limit > 0) ? out.slice(0, limit) : out;
}

function getNote(Notes, {id}) {
  return full(findNoteById(Notes, id));
}

function createNote(Notes, {name, body, folder}) {
  const props = {name: name || 'Untitled', body: body || ''};
  const at = folder ? findFolderByName(Notes, folder) : undefined;
  const created = at
    ? Notes.make({new: 'note', at: at, withProperties: props})
    : Notes.make({new: 'note', withProperties: props});
  return full(created);
}

function updateNote(Notes, {id, name, body, mode}) {
  const note = findNoteById(Notes, id);
  if (name != null) note.name = name;
  if (body != null) {
    note.body = (mode === 'append') ? (note.body() + body) : body;
  }
  return full(note);
}

function deleteNote(Notes, {id}) {
  const note = findNoteById(Notes, id);
  Notes.delete(note);
  return {id: id, deleted: true};
}

function listFolders(Notes) {
  return Notes.folders().map(f => ({id: f.id(), name: f.name()}));
}

function searchNotes(Notes, {query, limit}) {
  if (!query) throw new Error('query is required');
  const q = String(query).toLowerCase();
  const cap = (limit && limit > 0) ? limit : 25;
  const notes = Notes.notes();
  const matches = [];
  for (const n of notes) {
    const name = (n.name() || '').toLowerCase();
    let text = '';
    try { text = (n.plaintext() || '').toLowerCase(); } catch (_) {}
    if (name.includes(q) || text.includes(q)) {
      matches.push(summary(n));
      if (matches.length >= cap) break;
    }
  }
  return matches;
}
