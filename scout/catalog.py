"""One real product watch. Reported economics remain separate from live reads."""
PRODUCT = {
    "id": "matcha-watch", "name": "Café Kitsuné × Casio", "subtitle": "The matcha watch",
    "sku": "AQ-230ECK-3A", "target_sgd_cents": 24000,
    "image": "assets/matcha-watch.png", "status": "watching_restock",
    "description": "A matcha-green dial, silver bracelet and the Café Kitsuné fox. A small collaboration worth keeping an eye on.",
    "reported_buy_eur_cents": 9990, "reported_buy_sgd_cents": 14500,
    "reported_demand_low_sgd_cents": 24000, "reported_demand_high_sgd_cents": 35000,
    "economics_provenance": "User-provided research; FX, European price and Singapore bids not independently confirmed.",
}
PRODUCTS = {PRODUCT["id"]: PRODUCT,
    "raspberry-pi-5": {"id":"raspberry-pi-5","name":"Raspberry Pi 5","subtitle":"8GB board","sku":"DEMO-PI5-8GB","image":"assets/pi-board.svg","description":"The 8GB board from our earlier demo catalog. Sample sourcing, price and inventory."},
    "keyboard-kit": {"id":"keyboard-kit","name":"Mechanical keyboard kit","subtitle":"Your next desk upgrade","sku":"DEMO-KEYBOARD-KIT","image":"assets/keyboard.svg","description":"A mechanical keyboard kit from our earlier demo catalog. Sample sourcing, price and inventory."},
    "raspberry-pi-zero": {"id":"raspberry-pi-zero","name":"Raspberry Pi Zero 2 W","subtitle":"Small board, big ideas","sku":"DEMO-PI-ZERO-2W","image":"assets/pi-zero.svg","description":"The compact board from our earlier demo catalog. Sample sourcing, price and inventory."},
}
SOURCES = [
    {"id": "europe", "name": "Europe Scout", "market": "Europe", "currency": "EUR",
     "url": "https://www.casio.com/europe/watches/casio/product.AQ-230ECK-3A/", "adapter": "casio_html",
     "reported_price_minor": 9990, "reported_stock": "sold_out", "color": "green"},
    {"id": "us", "name": "US Scout", "market": "United States", "currency": "USD",
     "url": "https://www.casio.com/us/watches/casio/product.AQ-230ECK-3A/", "adapter": "casio_html",
     "reported_price_minor": None, "reported_stock": "unknown", "color": "purple"},
    {"id": "australia", "name": "Australia Scout", "market": "Australia", "currency": "AUD",
     "url": "https://www.shopcasio.shriro.com.au/products/casio-aq230eck-3a-watch", "adapter": "shopify",
     "feed": "https://www.shopcasio.shriro.com.au/products/casio-aq230eck-3a-watch.js",
     "reported_price_minor": 24900, "reported_stock": "sold_out", "color": "amber"},
]
ALIASES = {"AQ230ECK3A", "AQ230ECK3AER"}
