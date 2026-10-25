# ComfyUI App Mode (Linear Mode) & UI Specifications

This document defines the architecture, data schemas, and strict serialization invariants for **App Mode** (also known as **Linear Mode**) in ComfyUI, as implemented and integrated within Comfy Colab Kit.

---

## 🧭 Overview & Design Philosophy

ComfyUI provides two distinct user interfaces over the same underlying node graph:
1. **Graph Mode (Node Canvas)**: The classic LiteGraph visual noodle graph showing nodes, slots, and links.
2. **App Mode (Linear Mode)**: A clean, streamlined task form that hides node spaghetti and exposes only designated input controls (prompts, seeds, dimensions, sliders, upload buttons) and final output previews.

Comfy Colab Kit boots workflows into App mode by default (`extra.linearMode = true`), making generative AI pipelines directly accessible to end users and agents without visual clutter.

---

## 📐 Serialization Specification (`extra.linearData`)

In ComfyUI workflow JSONs, App Mode metadata resides exclusively inside the top-level `extra` block. The node list itself remains unmodified:

```json
{
  "last_node_id": 26,
  "last_link_id": 30,
  "nodes": [ ... ],
  "links": [ ... ],
  "extra": {
    "linearMode": true,
    "linearData": {
      "inputs": [
        [6, "value"],
        [8, "text"],
        [1, "choice"],
        [22, "value"],
        [23, "width"],
        [23, "height"],
        [23, "batch_size"],
        [30, "image"]
      ],
      "outputs": [
        26
      ]
    }
  }
}
```

### Schema Rules

| Property | Type | Description |
|---|---|---|
| `extra.linearMode` | `boolean` | When `true`, ComfyUI opens in App Mode on page load. |
| `extra.linearData.inputs` | `Array<[number \| string, string, object?]>` | Tuples mapping `[node_id, widget_name, optional_config]`. |
| `extra.linearData.outputs` | `Array<number \| string>` | Node IDs that produce the outputs shown in the App preview/history panel. |

---

## 🚨 Critical Architectural Invariants

### Rule 1: Never Serialize `widgets` on Node Dictionaries
```python
# ❌ NEVER DO THIS — FATAL BUG:
node["widgets"] = [{"name": wname, "label": label}]
```

* **Why**: In ComfyUI / LiteGraph serialization specifications (`ISerialisedNode`), a node JSON dictionary **never contains a `widgets` key**. Node parameter values are stored in `widgets_values: [...]`.
* **The Failure Mechanism**: Runtime `node.widgets` in JavaScript contains live `IBaseWidget` class instances (`type`, `value`, `options`, `draw()`, `callback()`, `computeSize()`). When `widgets` is written into the JSON, LiteGraph's deserializer (`hydrateExtensionPayload` in `src/lib/litegraph/src/extensionPersistence.ts`) treats `widgets` as an unknown extension and executes:
  ```javascript
  Object.assign(this, { widgets: [{ name: wname, label: label }] })
  ```
  This clobbers all real widget instances with hollow dictionaries lacking `.type`, `.value`, and callbacks.
* **The Symptom**: When `WidgetGrid.vue` calls `processWidget(id, ctx)` in `processedWidgetRenderModel.ts`:
  ```typescript
  const type = liveWidget?.type ?? widgetState.type;
  if (!type) return null; // Clones with no type are dropped!
  ```
  Every single widget returns `null`. The App Mode sidebar renders **only the title bar** (`AppModeWidgetList.vue`), while all input boxes, textareas, sliders, and upload drop zones vanish completely.

---

### Rule 2: Backing Input Slots Must Match LiteGraph's Widget-Slot Schema
```python
# ✅ CANONICAL BACKING SLOT FOR WIDGET LABELS:
node["inputs"].append({
    "name": wname,
    "type": slot_type,
    "widget": {"name": wname},
    "label": label,
    "link": None
})

# ❌ NEVER DO THIS (missing "widget": {"name": wname} property):
node["inputs"].append({"name": wname, "type": "STRING", "label": label})
```

* **Why**: In ComfyUI LiteGraph, widget labels round-trip through `node.inputs` via slots designated with `"widget": {"name": wname}`.
* **The Mechanism**: During node deserialization in LiteGraph (`configure()` in `src/lib/litegraph/src/LGraphNode.ts`):
  ```typescript
  if (this.widgets) {
    for (const [index] of this.widgets.entries()) {
      const w = legacyArrayItem(this.widgets, index);
      if (!w) continue;
      const input = this.inputs.find((i) => i.widget?.name === w.name);
      if (input?.label) w.label = input.label;
    }
  }
  ```
  App Mode (`AppModeWidgetList.vue`) then displays `action.widget.label || action.widget.name`.
* **Important Detail**: If a slot lacks the inner `"widget": {"name": wname}` dict, `isWidgetInputSlot(slot)` returns `false` and LiteGraph treats it as a raw disconnected connection socket rather than a widget binder.

---

### Rule 3: Native Media Loaders Require Untouched Node Primitives
For image, video, and audio inputs (`LoadImage`, `LoadVideo`, `LoadAudio`):
* App Mode delegates media rendering and upload to `getLoaderDropIndicator.ts` and `DropZone.vue`.
* The upload trigger binds to `node.widgets?.find(w => w.name === 'upload')?.callback`.
* Drag-and-drop file acceptance relies on `node.onDragOver` and `node.onDragDrop`.
* Overwriting `node.widgets` breaks the internal `upload` button and prevents the upload area from appearing.

---

### Rule 4: Renaming & Labels in App Mode
If descriptive or localized titles are desired:
1. **Explicit Custom Label in `profile.yaml`**: `app.inputs` supports 3-tuples `[node_id, widget_name, label]`, e.g. `[6, value, "正向提示词 (Positive Prompt)"]`.
2. **Automatic Canonical Inference**: If the 3rd element is omitted, `decorate_app_nodes()` in `setup.py` automatically derives descriptive bilingual labels (e.g. `正向提示词 (Positive Prompt)`, `负面提示词 (Negative Prompt)`, `随机种子 (Seed)`, `图像宽度 (Width)`, `图像高度 (Height)`, `画质与速度预设 (Preset)`).
3. **Node Title (Safe & Visible in Graph/Builder)**: `node["title"]` is safely updated in the graph JSON so node headers remain clear in Graph and Builder views.
4. **App Builder Renaming**: Users and agents can use ComfyUI's built-in App Builder ("构建应用") to rename widgets, which persists labels cleanly via `promptRenameWidget()` and updates the backing slot.

---

## 🔍 How to Inspect & Validate UI Specifications

When maintaining workflows or verifying App Mode compatibility:

1. **Verify JSON Cleanliness**:
   Ensure nodes in `workflows/*.json` contain no injected `widgets` keys:
   ```bash
   python3 -c "
   import json
   with open('path/to/workflow.json') as f:
       d = json.load(f)
   for n in d.get('nodes', []):
       assert 'widgets' not in n, f'Node {n.get(\"id\")} contains invalid widgets key'
   print('Workflow nodes are clean.')
   "
   ```

2. **Validate Profile Workflows**:
   Run the repository's profile validator:
   ```bash
   python scripts/validate-profile.py
   ```

3. **Run Unit Tests**:
   Ensure setup helpers and workflow preparers preserve node hygiene:
   ```bash
   python3 -m pytest tests/test_setup_helpers.py -v
   ```

4. **Upstream Source of Truth**:
   The authoritative implementation of App Mode (Linear Mode) resides in the [`Comfy-Org/ComfyUI_frontend`](https://github.com/Comfy-Org/ComfyUI_frontend) repository:
   - `src/renderer/extensions/linearMode/`: App Mode layout, preview, history, and controls.
   - `src/components/builder/`: App Builder, `AppModeWidgetList.vue`, and input resolution.
   - `src/types/widgetId.ts`: Canonical widget identification specification (`graphId:nodeId:name`).
   - `browser_tests/`: Playwright end-to-end test fixtures for App Mode.
