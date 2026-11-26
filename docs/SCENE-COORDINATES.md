# Scene coordinates and transforms

## atlas-anatomy-y-up-v1

This profile is right-handed. Directions refer to the patient.

| Positive axis | Direction |
| --- | --- |
| X | Patient left |
| Y | Superior |
| Z | Anterior |

Scene translations use meters (`m`). Angles used to construct rotations use radians (`rad`). The quaternion and scale components are dimensionless. Each scene version records the profile, units, and an explicit description of its origin. The origin identifies the reference point for that version, such as a particular landmark or a point in a named reference frame.

### Local and world transforms

Local transforms use glTF-compatible XYZ translation, XYZW quaternion rotation, and XYZ scale. The identity values are:

| Field | Identity |
| --- | --- |
| `translation` | `[0, 0, 0]` |
| `rotation` | `[0, 0, 0, 1]` |
| `scale` | `[1, 1, 1]` |

Use homogeneous **column vectors**, with points represented as `[x, y, z, 1]ᵀ`. The local matrix is `M_local = T × R × S`. A local point maps into the parent frame as `p_parent = M_local × p_local`. The rightmost operation acts first, so this applies scale, then rotation, then translation. Translation occupies the last column. If matrices are serialized as glTF arrays, use glTF's column-major storage order.

A root's world matrix equals its local matrix. For a child, `M_world_child = M_world_parent × M_local_child`. For deeper nodes, multiply the local matrices in root-to-node order. For example, take a root with translation `[10, 0, 0]` and a rotation of `π/2` about +Z. A child translated by `[2, 0, 0]` in the root frame has world origin `[10, 2, 0]`, in meters.

Translation and scale each contain three finite components. Rotation contains four finite components and must satisfy `abs(x² + y² + z² + w² − 1) ≤ 0.000001` (absolute tolerance on the squared norm). This excludes zero-length quaternions. Normalize the accepted quaternion before forming a rotation matrix to avoid introducing distortion from numerical rounding. The database rejects zero or negative scale. This anatomy profile also requires exact unit scale on every node, including ancestor groups. Source-unit conversion belongs in the reviewed asset preparation step. Use a new asset version when normalization or any other edit changes GLB bytes. [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#transformations)

### DICOM LPS compatibility

For patient-frame DICOM coordinates `[L, P, S]` measured in millimeters:

```text
Atlas X =  DICOM L / 1000
Atlas Y =  DICOM S / 1000
Atlas Z = -DICOM P / 1000
```

The axis mapping preserves handedness. For example, `[10, 20, 30] mm` becomes `[0.01, 0.03, -0.02] m` and `[-250, -100, 1250] mm` becomes `[-0.25, 1.25, 0.10] m`. These formulas preserve the source origin. For voxel indices, first use the dataset's image geometry to compute DICOM patient-frame coordinates. Apply the conversion to those coordinates. Anatomical registration is a separate editorial operation that establishes correspondence between source and target frames and verifies landmarks, pose, orientation, and origin. [DICOM PS3.3 Image Plane Module](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html)

## Scene graph and anatomy

A scene has a stable public code and one or more identified versions. A version contains zero or more roots. Each node has a public code unique within that version and at most one parent in the same version. A parent-child edge controls spatial inheritance and grouping. Scientific relationships such as `part_of` and `is_a` are recorded in the P1.1 anatomy model. A spatial parent edge carries no scientific classification.

An asset node pins an exact P1.2 asset version. A group may also carry a pin. Scene nodes use the same structure independently of the scientific domain. The separate anatomy mapping table references an existing P1.2 anatomy asset link. PostgreSQL checks that its asset version matches the node's pin and preserves the linked structure identity. Each draft mapping save captures the registry role, including a null role. The API returns that saved role with the structure code. Later role changes in the registry affect the scene only if an editor saves the mapping again while the scene version is still a draft.

Node pages sort by `sort_order` and then node code. A parent can occur on a different page. Fetch all pages for the same scene code and version, then resolve parent codes within that version before composing world transforms.

## Editorial spatial verification

1. Create a draft scene version with a public origin description. Identify the origin landmark or reference frame precisely and record the source coordinate system, units, and provenance in the editorial record.
2. Verify each asset's authoring units, axis directions, handedness, internal glTF transforms, and pivot. A GLB has its own origin. Record how it maps into the scene frame, including any basis or unit conversion applied during asset preparation and any reviewed local translation and rotation used for alignment. Check internal geometry and node transforms for unintended reflection or distortion.
3. Verify visible left and right structures, landmark alignment, and representative metric distances after composing the scene hierarchy. Record reference measurements and the evidence or tolerances used for the spatial review in a durable internal record. Verify any anatomy mappings and their saved roles against the existing anatomy and asset records. Every mapped structure must be published before publishing the scene version.
4. Record the internal `spatial_review_reference` after the graph and origin edits are complete, then publish the version through the controlled database workflow. Editing the origin, nodes, or mappings in a draft clears the stored review reference. Each pinned asset and its exact version must be published and satisfy P1.2 eligibility checks. Publish the scene record to make its eligible versions discoverable. Keep filesystem paths and confidential evidence in the internal record. [3D Asset Registry editorial workflow](../backend/README.md#editorial-publication)

A published version freezes the version metadata, graph, transforms, asset and anatomy references, and saved annotation roles. To make an edit, create a new draft version and preserve node codes when they continue to identify the same node. A published version can move to `withdrawn` while retaining its content, including annotation roles. Withdrawn version identifiers remain reserved. Graph writes and publication serialize per version. A serialization failure or deadlock requires retrying the editorial transaction from a fresh snapshot.

The database checks structural integrity, numerical constraints, recorded review, and registry eligibility. Origin choice, anatomical registration, semantic plausibility, and GLB-internal geometry and transforms require editorial verification. As with P1.2, the public API relies on registry state for file availability. Operators should withdraw an asset version when its verified bytes are unavailable.

## Public API

| GET path relative to `/api/v1` | Response |
| --- | --- |
| `/scenes` | Page of published scenes in code order, with the newest eligible version of each. |
| `/scenes/{code}` | Scene metadata and a page of eligible versions by descending creation time, then descending version identifier. |
| `/scenes/{code}/versions/{version}` | Identity, coordinate and unit metadata, and origin for the exact published version. |
| `/scenes/{code}/versions/{version}/nodes` | Page of nodes with local transforms, parent codes, exact asset references, and saved anatomy annotations. |

Pages use `limit` (default 50, maximum 100) and `offset` (default 0) and return `items`, `limit`, `offset`, and `has_more`. Invalid arguments return HTTP 422. Missing, draft, or withdrawn scenes or versions return HTTP 404. Database failures return HTTP 503. Public responses expose asset codes and version identifiers. Storage references and internal review records stay internal. [OpenAPI v1](../contracts/openapi.v1.json)

Every mapped structure is a required dependency. If an asset pin becomes ineligible or a mapped structure goes back to draft, discovery omits the entire version. The exact version and every node page return HTTP 503, including pages with no affected nodes. The response detail is `Scene asset versions unavailable.` for an ineligible asset pin and `Scene anatomy annotations unavailable.` for an unpublished structure when all asset pins are eligible. Scene detail returns HTTP 503 when it has published versions but none are eligible. If at least one of those versions has eligible asset pins, that response identifies the anatomy dependency. Republish the structure to restore the same saved annotations for any scene version that is still published and eligible.

Use the [scene manifest](SCENE-MANIFEST.md) to retrieve the complete offline scene and its exact asset URLs in one request.

Revision `0004` adds the scene tables to the `atlas` schema under `atlas_owner`. Downgrading to `0003` removes the scene tables and their data and preserves existing anatomy and asset records.
