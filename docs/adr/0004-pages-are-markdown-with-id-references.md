# Pages are Markdown with ID-based references

Page content is stored as Markdown text. Mentions and Live Fields are written in a NetAtlas-specific syntax that refers to items by their ID; the editor shows the item's current name. We chose Markdown over a block-based rich-text editor storing JSON (TipTap/ProseMirror) because it diffs cleanly in History, stays portable and readable outside NetAtlas, and renders simply on the server. Referring to items by ID is what lets a Mention or Live Field follow an item through renames and show as broken once the item is deleted.
