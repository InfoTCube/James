# biedronka

- **Source:** the food leaflets ("Codziennie niskie ceny") listed on `biedronka.pl/pl/gazetki`. Each
  leaflet's page has an id for `leaflet-api.prod.biedronka.cloud`, which lists its page images.
  They are images only, about 100 pages each. The text comes from local OCR
  (`rapidocr-onnxruntime`), so no Pro quota is used. robots.txt only disallows the site's search.
- **Why not elsewhere:** `zakupy.biedronka.pl` has text but only about 29 promotions, with no
  regular prices or dates, for an online shop that doesn't deliver everywhere. Ding.pl has no
  products from the current food leaflets. The Biedronka app has no public API.
- **Schedule:** hourly 01:35–05:35, at most 60 pages per run, so all pages are read within a night.
  Read pages are kept, and removed when the leaflet leaves the website. A page takes 3–6 s on a
  laptop and more on the mini PC. It also runs at worker start, so a restart can keep the CPU busy
  for a while. It does nothing until you add a product.
- **Products** (`biedronka_products`): `/buy masło, mleko, kawa` adds a whole list, for example
  copied from your purchase history. `/buy` alone lists them, `/buy_del masło` removes one. Use
  short Polish words: `ser` also finds "serek" but not "deser", and one misread letter is allowed
  in words of 4+ letters.
- **Matching** (`service.find_on_page`): done on read, so a new product is found in pages already
  read. Each big price is paired with the nearest line naming the product, then the nearest
  discount and old price. New offers go to Telegram in one message.
- **Shown:** dashboard card (every 3rd round, tap an offer to get its page on Telegram), `/promos`,
  voice ("anything on promotion?").
- **Fragility:** high. OCR misreads Polish letters ("Masto" for "Masło"), and the price pairing is
  a layout guess, so a word on another product's packaging can match, or a price can be missed.
  Every offer links its page image: check it before relying on a price. Many prices also need the
  Moja Biedronka card or a multi-buy (2,99 zł "przy zakupie 3"), which isn't shown. If the leaflet
  names change, the run fails and the card goes stale; the fix is `PRESS` in collector.py.
