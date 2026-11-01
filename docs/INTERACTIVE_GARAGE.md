# Interactive Garage

The Interactive Garage vehicle builder (interactivegarage.com/dreamauto) is shown on its own page,
`/build` ("Build your dream ride" in the Add a Vehicle bar). My Garage (`/garage`) holds the shopper's
vehicles and **saved builds**.

The builder runs on Interactive Garage's site inside a frame, so this site can't read what the shopper
does in it. Builds reach the store when the builder sends them here:

## For Interactive Garage: sending a build to the store

Send the shopper (a link or form in the top window, `target="_top"`) to:

```
https://dream-auto-builders.onrender.com/build/import
```

GET or POST, with these fields:

| Field | Required | Example | Meaning |
|---|---|---|---|
| `items` | yes* | `69-1935:1,331035001:4,250572:4` | Part numbers with quantities, comma separated (`:qty` optional, default 1) |
| `pn`, `qty` | yes* | `pn=69-1935&qty=1&pn=331035001&qty=4` | Same thing as repeated fields, instead of `items` |
| `year`, `make`, `model` | no | `2022`, `Ram`, `1500` | The vehicle in the build. If we sell parts for it, it becomes the shopper's selected vehicle |
| `vehicle` | no | `2022 Ram 1500 Laramie` | Vehicle as text, if year/make/model aren't separate |
| `name` | no | `Ram trail build` | Name for the saved build |
| `brand` | no | `ReadyLift` | Used only when two brands share a part number |
| `action` | no | `cart` | `cart`: save the build **and** add its parts to the cart, then open the cart. Without it: save the build and open My Garage |

\* `items` or `pn`. Part numbers are matched ignoring dashes, spaces and capitals, against the
manufacturers' part numbers from SEMA Data. Parts the store doesn't sell are listed as "not sold
here yet" on the saved build.

Example ("Add build to cart" button):

```html
<a target="_top" href="https://dream-auto-builders.onrender.com/build/import?action=cart&year=2022&make=Ram&model=1500&name=Ram%20trail%20build&items=69-1935:1,331035001:4,250572:4">Add build to cart</a>
```

When the store moves to its own domain, only the address changes.
