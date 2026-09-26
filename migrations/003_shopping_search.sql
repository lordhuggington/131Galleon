-- v3: the list is ordered on Amazon Fresh, so each shopping item carries the search
-- phrase that lands on the right product. Plans written before this keep an empty
-- phrase; the app falls back to the item name and the quantity.

ALTER TABLE shopping_items ADD COLUMN search TEXT NOT NULL DEFAULT '';
