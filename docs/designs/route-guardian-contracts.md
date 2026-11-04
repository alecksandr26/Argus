# Route guardian contracts (2026-11-03)

Every new route gets a **guardian**: a `guardian`-role user who watches that route. The guardian's
phone is the number the truck's ESP32 texts on every critical alert. User decisions behind this:

- The phone reaches the ESP32 **only through `argus-device.env`** (`ARGUS_ALERT_PHONE`). The Pi
  sends it in `CONFIG` as `alert_phone`, as today. No new backend→device field and no firmware
  change for the phone.
- Guardian phones must be **E.164** (`ALERT_PHONE_PATTERN`, `^\+[1-9][0-9]{7,14}$`).
- Routes that already exist without a guardian keep working. The UI warns and offers
  "Assign guardian". The env file falls back to `Truck.alert_phone`.
- The simulator has its own default guardian with a fake E.164 phone.

The sections below are the contracts the backend, UI, simulator and it-argus build against.
Field names are exact.

## G1. Backend data model

- `Route.id_guardian: Optional[str] = None`: a User id. Optional **in the model** so older
  documents and direct `Route(...)` constructions still load.
- `RouteCreate.id_guardian: str`: **required** (a missing one is 422).
- `RouteUpdate.id_guardian: Optional[str]`: assign or change the guardian. Rejected with 409 on a
  route whose `operative_status` is `completed` or `stopped` (`"route is finished"`).
- Validation on create and update (422, `detail` as quoted). Order: missing, then wrong role or
  inactive, then phone:
  - `"id_guardian not found"`: no such user.
  - `"id_guardian must be an active guardian"`: the user isn't `role == guardian` or isn't
    `is_active`.
  - `"guardian has no valid E.164 phone"`: the user's `phone_number` fails `ALERT_PHONE_PATTERN`
    (after normalisation, G2).
- Write RBAC is unchanged: `root_admin` and `admin` create and update routes.

## G2. Guardian phone format

- A shared helper, `normalize_phone(value) -> str`, lives in a new `app/phone.py`.
  - It strips surrounding whitespace, then removes inner spaces, `-`, `.`, `(` and `)`.
  - `app/schemas/truck.py`'s `_alert_phone` reuses it, and keeps its existing behaviour of
    turning `""` into `None`.
- On `POST /api/users` and `PUT /api/users/{id}` (and `PUT /api/auth/me`):
  - When the **resulting** role is `guardian`, `phone_number` is normalised and must match
    `ALERT_PHONE_PATTERN`, else 422 `"guardian phone_number must be in E.164 format, e.g.
    +523312345678"`.
  - Other roles keep free-text phones, unchanged.
  - An update that touches neither `role` nor `phone_number` never fails on an old bad phone.
- Normalised phones are stored normalised.

## G3. Guardian lifecycle guard

These return **409** `"guardian is assigned to N active route(s); reassign them first"` while the
guardian is assigned to any route with `operative_status` in `{scheduled, in_progress}`:

- `PUT /api/users/{id}` with `is_active: false`, or with a role other than `guardian`.
- `DELETE /api/users/{id}`.

## G4. Route responses

`RouteOut`, and with it `RouteWithStatus`, `RouteLive` (`/api/live`) and the device
`active-route`, gains:

```json
"id_guardian": "665f...|null",
"guardian": { "id_user": "665f...", "first_name": "Ana", "last_name": "López",
              "phone_number": "+523312345678" } | null
```

- `guardian` is null when `id_guardian` is null or the user no longer exists.
- It is resolved server-side so guardian-role viewers, who can't call `/api/users`, see it too.

## G5. Env-file phone resolution: `POST /api/trucks/{id}/rotate-key`

- Request:
  - An optional JSON body `{"id_route": "..."}`. A missing body or `{}` behaves as today.
  - Unknown route, or a route of another truck: 422 `"id_route not found for this truck"`.
- Response (`DeviceKeyResponse`): the existing `id_truck` and `device_api_key`, plus:

```json
"alert_phone": "+523312345678" | null,
"alert_phone_source": "route_guardian" | "truck" | null,
"id_route": "..." | null,
"guardian_name": "Ana López" | null
```

Resolution, first match wins:
1. `id_route` was given, and that route's guardian exists, is active and has a valid phone:
   `route_guardian`.
2. Else the truck's `in_progress` route (`active_route_for`), if its guardian is usable.
3. Else the truck's earliest `scheduled` route by `estimated_departure`, if its guardian is
   usable.
4. Else `Truck.alert_phone`, if set: `truck` (`id_route` is still the route considered, or null).
5. Else everything is null.

`id_route` and `guardian_name` describe the route and guardian whose phone was picked. For a
`truck` fallback they name the route considered, with `guardian_name: null`.

## G6. `/health` version

- `GET /health` adds `"version": "<git sha or 'dev'>"`.
- The value comes from env `ARGUS_VERSION`, which the backend Dockerfile sets from build arg
  `ARGUS_VERSION` (default `dev`).
- `infra-argus/scripts/deploy.sh` passes the git short SHA to the backend image build and to the
  UI build as `VITE_GIT_SHA`.

## G7. UI

- **Routes screen:**
  - The create form has a required **Guardian** select (active guardians; ones without a valid
    phone are disabled, marked "no valid phone").
  - The list has a Guardian column.
  - An "Assign guardian" action is offered for `scheduled` and `in_progress` routes.
  - A warning banner shows when any active route has no guardian.
- **Live operations and route views:** show the embedded `guardian`.
- **Access:** an E.164 check and hint when the role is `guardian`.
- **argus-device.env:**
  - Built from the rotate-key response's `alert_phone`, not `truck.alert_phone`, plus a comment
    line naming the source:

    ```
    # SMS alert phone: guardian Ana López of route <id>
    # SMS alert phone: the truck's fallback phone
    ```

    When there's no phone, the existing commented `ARGUS_ALERT_PHONE=` line stays.
  - The issued key survives the truck form's Save. The download starts as soon as the key is
    issued.
- **`RouteDeviceFileButton`** (`src/components/RouteDeviceFileButton.tsx`):
  - Props: `{ route: Route; truck: Truck; token: string; onTruckChange?: (t: Truck) => void }`.
  - Rotates the truck's key with that `id_route` and shows the same credentials panel.

## G8. Simulator

- It finds or creates a guardian `SIMULATOR_GUARDIAN_EMAIL` (default `sim-guardian@argus.sim`,
  "SIM Guardian", phone `SIMULATOR_GUARDIAN_PHONE`, default `+520000000000`), via the root_admin
  login it already uses.
- An existing one is reactivated and its phone fixed when needed.
- Every route it provisions carries its `id_guardian`.
- `reset_previous_run` keeps the guardian, since it is reused across runs.

## G9. ESP32 (later)

- No protocol change.
- Once the other ESP32 branch merges: clearer `[sms]` Serial logs (phone masked, text, attempt,
  result, and why an SMS was skipped).
- Check that the SMS text matches the alert the backend gets.
