# mixed_non_food_01 (placeholder)

Needs `receipt.jpg`: a big-box receipt (Target/Walmart) mixing groceries with
household goods, a bag fee and a coupon. Scores `is_food` precision/recall;
the app only surfaces `is_food: true` rows, so a non-food line leaking through
is a user-visible regression.
