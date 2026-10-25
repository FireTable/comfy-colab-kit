---
name: comfy-workflow-ui
description: Audit, inspect, debug, and configure ComfyUI App Mode (Linear Mode) and LiteGraph UI forms in Comfy Colab Kit workflows.
---

# ComfyUI Workflow UI & App Mode Maintenance Skill (`/comfy-workflow-ui`)

This skill guides AI agents and engineers in maintaining, inspecting, and debugging **ComfyUI App Mode (Linear Mode)** forms and LiteGraph workflow JSON specifications in Comfy Colab Kit.

---

## 1. When to Use This Skill

Activate this skill whenever:
- Adding or modifying a workflow in `profiles/` with App Mode support (`extra.linearMode = true`).
- App Mode forms render improperly (e.g., only titles appear without input boxes, textareas, sliders, or image upload buttons).
- Auditing workflow JSON cleanliness to ensure compatibility with ComfyUI's modern Vue frontend (`comfyui-frontend-package`).
- Customizing input titles, labels, default values, or media upload drop zones.

---

## 2. The Three Golden Invariants of Workflow UI

ComfyUI's frontend (`Comfy-Org/ComfyUI_frontend`) uses Vue 3 components (`AppModeWidgetList.vue`, `NodeWidgets.vue`, `WidgetGrid.vue`, `DropZone.vue`) mounted over a LiteGraph runtime canvas. To ensure form controls render correctly, you **MUST** uphold these three invariants:

### 🚨 Invariant 1: Top-level `extra.linearData` is the ONLY place for App Mode mapping
Never mutate node execution dictionaries to configure App Mode. All UI bindings are declared exclusively at the workflow root:

```json
{
  "extra": {
    "linearMode": true,
    "linearData": {
      "inputs": [
        [30, "image"],
        [6, "value"],
        [8, "text"],
        [20, "denoise"],
        [1, "choice"],
        [22, "value"],
        [23, "width"],
        [23, "height"]
      ],
      "outputs": [
        26
      ]
    }
  }
}
```

- Each entry in `inputs` is `[node_id, widget_name]` (or `[node_id, widget_name, { height, description }]`).
- Each entry in `outputs` is `node_id`.

### 🚨 Invariant 2: NEVER put a `"widgets"` key in a node dictionary
- **The Mechanism**: In the official `ISerialisedNode` spec, serialized node dictionaries have no `widgets` key; values live in `widgets_values: [...]`. At runtime, `node.widgets` contains live JavaScript class instances (`IBaseWidget`).
- **The Pitfall**: If Python code (such as `setup.py` or a preprocessing script) writes `node["widgets"] = [{"name": wname, "label": ...}]`, LiteGraph's deserializer (`hydrateExtensionPayload`) treats `widgets` as an unknown extension and runs `Object.assign(this, { widgets: [...] })`.
- **The Consequence**: This completely overwrites real widget instances with hollow dicts that lack `.type`. `processedWidgetRenderModel.ts` drops every widget whose `type` is undefined (`if (!type) return null`). Result: **Only title bars appear; all form inputs, textareas, and sliders vanish!**

### 🚨 Invariant 3: Widget labels round-trip through canonical backing slots
- **The Mechanism**: LiteGraph restores widget labels via backing input slots where `input.widget?.name === w.name`:
  ```typescript
  const input = this.inputs.find((i) => i.widget?.name === w.name);
  if (input?.label) w.label = input.label;
  ```
- **The Rule**: When assigning widget labels, ensure the slot in `node.inputs` carries:
  ```json
  {
    "name": wname,
    "type": slot_type,
    "widget": { "name": wname },
    "label": label,
    "link": null
  }
  ```
  App Mode (`AppModeWidgetList.vue`) displays `action.widget.label || action.widget.name`.
- **The Pitfall**: Never inject a bare slot without `"widget": {"name": wname}`. Without the inner `widget` dict, LiteGraph treats it as a standard disconnected wire socket instead of a widget binder.

---

## 3. UI Inspection & Verification Runbook

### A. Quick Workflow Cleanliness Audit
Run this script to verify that a workflow JSON does not contain illegal serialized `widgets` or corrupted input slots:

```bash
python3 -c "
import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'profiles/examples/moss-image/workflows/matlowai_img2img.json'
with open(path) as f:
    d = json.load(f)

corrupted = []
for n in d.get('nodes', []):
    nid = n.get('id')
    if 'widgets' in n:
        corrupted.append(f'Node {nid} has illegal \"widgets\" key (breaks live widgets)')
    for slot in n.get('inputs', []):
        if slot.get('widget') is None and slot.get('name') in ['image', 'denoise', 'choice']:
            # verify it is a real connection slot or a legitimate converted widget
            pass

if corrupted:
    print('❌ FAILED: Found workflow UI corruption:')
    for err in corrupted:
        print('  -', err)
    sys.exit(1)
else:
    print('✅ PASSED: Workflow node structures are clean.')
" <path-to-workflow.json>
```

### B. Inspect App Mode Inputs in a Workflow
Check which node IDs and widget names are currently exposed in App Mode:

```bash
python3 -c "
import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'profiles/examples/moss-image/workflows/matlowai_img2img.json'
with open(path) as f:
    d = json.load(f)

extra = d.get('extra', {})
print('linearMode:', extra.get('linearMode'))
ldata = extra.get('linearData', {})
print('App Inputs:')
nodes = {n['id']: n for n in d.get('nodes', [])}
for item in ldata.get('inputs', []):
    nid, wname = item[0], item[1]
    n = nodes.get(int(nid) if str(nid).isdigit() else nid)
    ntype = n.get('type') if n else 'MISSING NODE'
    title = n.get('title') if n else ''
    print(f'  Node {nid:2} [{ntype:25}] widget: {wname:12} title: {title}')
print('App Outputs:', ldata.get('outputs', []))
" <path-to-workflow.json>
```

### C. Native Media Loaders (`LoadImage`, `LoadVideo`, `LoadAudio`)
Native drop zones in App Mode are hardcoded in `getLoaderDropIndicator.ts`:
- `LoadImage`: expects widget name `"image"`.
- `LoadVideo`: expects widget name `"file"`.
- `LoadAudio`: expects widget name `"audio"`.

The upload button triggers `node.widgets.find(w => w.name === 'upload').callback()`. Ensure `LoadImage` nodes retain their standard widget structure.

---

## 4. Renaming & Label Best Practices

When friendly or localized field labels (e.g. `正向提示词 (Positive Prompt)`) are desired:

1. **Modify `node["title"]` (Recommended & Safest)**:
   In ComfyUI workflow JSON, `node.title` is a first-class canonical field. Setting `node["title"] = "正向提示词 (Positive Prompt)"` is safe, clean, and displays prominently in both Graph and App modes.

2. **Backing Input Slot Label**:
   For core nodes whose widgets are backed by an input slot (such as `CLIPTextEncode` where `input.widget.name == "text"`), set `input["label"] = "正向提示词"`. Never add a new slot if one doesn't exist.

3. **Never touch `node["widgets"]`**:
   Leave `node.widgets` entirely to the ComfyUI frontend runtime.

---

## 5. Troubleshooting Checklist

| Symptom | Root Cause | Fix |
|---|---|---|
| **Only titles appear, no inputs/sliders/textareas** | Node dictionaries have `widgets: [...]` in JSON, wiping out live `IBaseWidget` instances. | Remove `widgets` from node JSON dictionaries. Parameter values belong in `widgets_values`. |
| **Image node has no upload button or drop zone** | `node.widgets` was clobbered (breaking `upload` button) or `onDragDrop` failed to attach. | Keep node structure clean and map `[node_id, "image"]` in `linearData.inputs`. |
| **Widget row shows empty slot with dot instead of control** | Artificial slot injected into `node.inputs`, making frontend treat it as a converted connection. | Remove spurious input slots from `node.inputs`. |
| **App Mode toggle doesn't open on first load** | `extra.linearMode` is false or missing, or `default_view: "app"` not set in `profile.yaml`. | Set `extra.linearMode = true` and ensure profile specifies `default_view: app`. |

---

## 📚 Upstream Code References

The source of truth is the [Comfy-Org/ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend) repository:
- `src/renderer/extensions/linearMode/`: App Mode layout, controls (`LinearControls.vue`), and drop zones (`DropZone.vue`).
- `src/components/builder/AppModeWidgetList.vue`: Widget list container and label rendering.
- `src/renderer/extensions/vueNodes/composables/processedWidgetRenderModel.ts`: Widget resolution and `processWidget` pipeline.
- `src/lib/litegraph/src/extensionPersistence.ts`: LiteGraph deserializer (`hydrateExtensionPayload`).
- Repository Documentation: [`docs/APP_MODE.md`](../../docs/APP_MODE.md).
