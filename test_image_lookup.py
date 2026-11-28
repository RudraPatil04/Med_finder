"""Product photos are only taken from a listing that is the exact same product."""
import unittest
from unittest import mock

from backend.services import image_lookup

MEDICINE = {
    "id": 7, "medicine_name": "Crocin Quik 500mg Tablet", "brand_name": "Crocin Quik",
    "composition": "Paracetamol (500mg)", "dosage_form": "Tablet", "release_type": None,
    "image_url": "https://onemg.gumlet.io/a_ignore,w_380/hx2gxivwmeoxxxsc1hix.jpg",
}


def item(name, image, brand="CROCIN", composition="Paracetamol (500mg)"):
    return {"name": name, "consumerBrandName": brand, "image": image,
            "compositions": [{"name": composition}]}


PHOTO = "https://cdn01.pharmeasy.in/dam/products/1/crocin-quik.jpg?dim=80x80&f=jpg"


class HasRealImageTest(unittest.TestCase):
    def test_placeholder_and_empty_are_not_photos(self):
        self.assertFalse(image_lookup.has_real_image(MEDICINE["image_url"]))
        self.assertFalse(image_lookup.has_real_image(""))
        self.assertFalse(image_lookup.has_real_image("http://cdn01.pharmeasy.in/x.jpg"))
        self.assertFalse(image_lookup.has_real_image("https://cdn01.pharmeasy.in/placeholder.png"))
        self.assertTrue(image_lookup.has_real_image(PHOTO))


class ImageFromItemsTest(unittest.TestCase):
    def test_exact_product_photo_is_used_at_card_size(self):
        url = image_lookup.image_from_items([item("Crocin Quik 500Mg Strip Of 10 Tablets", PHOTO)], MEDICINE)
        self.assertEqual(url, PHOTO.replace("dim=80x80", "dim=300x0"))

    def test_sub_brand_is_rejected(self):
        listing = item("Crocin Quik Advance 500Mg Strip Of 10 Tablets", PHOTO)
        self.assertIsNone(image_lookup.image_from_items([listing], MEDICINE))

    def test_other_strength_is_rejected(self):
        listing = item("Crocin Quik 650Mg Strip Of 10 Tablets", PHOTO, composition="Paracetamol (650mg)")
        self.assertIsNone(image_lookup.image_from_items([listing], MEDICINE))

    def test_other_form_is_rejected(self):
        listing = item("Crocin Quik 500Mg Bottle Of 60ml Syrup", PHOTO)
        self.assertIsNone(image_lookup.image_from_items([listing], MEDICINE))

    def test_modified_release_listing_is_rejected(self):
        listing = item("Crocin Quik SR 500Mg Strip Of 10 Tablets", PHOTO)
        self.assertIsNone(image_lookup.image_from_items([listing], MEDICINE))

    def test_other_brand_is_rejected(self):
        listing = item("Dolo 500Mg Strip Of 15 Tablets", PHOTO, brand="DOLO")
        self.assertIsNone(image_lookup.image_from_items([listing], MEDICINE))

    def test_shared_stock_image_is_rejected(self):
        items = [item("Crocin Quik 500Mg Strip Of 10 Tablets", PHOTO),
                 item("Dolo 500Mg Strip Of 15 Tablets", PHOTO, brand="DOLO")]
        self.assertIsNone(image_lookup.image_from_items(items, MEDICINE))

    def test_missing_or_foreign_image_is_rejected(self):
        for image in ("", None, "https://example.com/crocin.jpg"):
            self.assertIsNone(image_lookup.image_from_items(
                [item("Crocin Quik 500Mg Strip Of 10 Tablets", image)], MEDICINE))


class FillImagesTest(unittest.TestCase):
    def setUp(self):
        image_lookup._cooldown_until = 0.0

    def test_found_photo_is_stored_and_attempt_recorded(self):
        with mock.patch.object(image_lookup, "_recently_attempted", return_value=False), \
                mock.patch.object(image_lookup, "find_listing_image", return_value=PHOTO), \
                mock.patch.object(image_lookup, "ensure_api_search_cache"), \
                mock.patch.object(image_lookup, "store_image") as store, \
                mock.patch.object(image_lookup, "record_attempt") as record:
            found = image_lookup.fill_images([MEDICINE])
        self.assertEqual(found, {7: PHOTO})
        store.assert_called_once_with(7, PHOTO)
        record.assert_called_once_with(7, True)

    def test_rows_with_a_photo_or_recent_attempt_are_skipped(self):
        with_photo = dict(MEDICINE, image_url=PHOTO)
        with mock.patch.object(image_lookup, "_recently_attempted", return_value=True), \
                mock.patch.object(image_lookup, "find_listing_image") as lookup:
            self.assertEqual(image_lookup.fill_images([with_photo, MEDICINE]), {})
        lookup.assert_not_called()

    def test_request_errors_start_a_cooldown(self):
        with mock.patch.object(image_lookup, "_recently_attempted", return_value=False), \
                mock.patch.object(image_lookup, "find_listing_image", side_effect=OSError("405")), \
                mock.patch.object(image_lookup, "ensure_api_search_cache"), \
                mock.patch.object(image_lookup, "store_image") as store:
            self.assertEqual(image_lookup.fill_images([MEDICINE]), {})
        store.assert_not_called()
        self.assertTrue(image_lookup.in_cooldown())


class EndpointTest(unittest.TestCase):
    def test_endpoint_returns_found_photos(self):
        from backend.app import app
        with mock.patch("backend.routes.medicine_routes.get_medicine", return_value=MEDICINE), \
                mock.patch("backend.routes.medicine_routes.fill_images", return_value={7: PHOTO}), \
                mock.patch("backend.routes.medicine_routes.live_prices_enabled", return_value=True):
            response = app.test_client().get("/api/medicine-images?ids=7,x,7")
        self.assertEqual(response.get_json()["images"], {"7": PHOTO})

    def test_endpoint_does_nothing_when_live_lookups_are_off(self):
        from backend.app import app
        with mock.patch("backend.routes.medicine_routes.live_prices_enabled", return_value=False), \
                mock.patch("backend.routes.medicine_routes.fill_images") as fill:
            response = app.test_client().get("/api/medicine-images?ids=7")
        self.assertEqual(response.get_json()["images"], {})
        fill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
