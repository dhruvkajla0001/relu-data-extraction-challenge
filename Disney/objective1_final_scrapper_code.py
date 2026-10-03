import json
import re
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

DISNEY_LIST_URL = (
    "https://disneycruise.disney.go.com/"
    "en-in/cruises-destinations/list/"
)

API_URL = (
    "https://disneycruise.disney.go.com/"
    "dcl-apps-productavail-vas/available-products/"
)

CDP_URL = "http://127.0.0.1:9222"

RAW_JSON_FILE = Path("disney_all_pages.json")
CSV_FILE = Path("disney_cruises_final.csv")
ANSWERS_FILE = Path("disney_objective1_answers.txt")


BASE_PAYLOAD = {
    "currency": "INR",
    "filters": [],
    "partyMix": [
        {
            "accessible": False,
            "adultCount": 2,
            "childCount": 0,
            "nonAdultAges": [],
            "partyMixId": "0",
        }
    ],
    "region": "INTL",
    "storeId": "DCL",
    "affiliations": [],
    "pageHistory": False,
    "includeAdvancedBookingPrices": True,
    "exploreMorePage": 1,
    "exploreMorePageHistory": False,
    "sorts": [
        {
            "criteria": "RECOMMENDED",
            "order": "ASC",
            "region": "PB",
        }
    ],
}


# ============================================================
# HELPERS
# ============================================================

def safe_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def clean(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return safe_json(value)

    return str(value).strip()


def recursive_text(obj):
    """
    Convert an object into searchable text.
    """
    if obj is None:
        return ""

    if isinstance(obj, str):
        return obj

    if isinstance(obj, (dict, list)):
        return safe_json(obj)

    return str(obj)


def unique_nonempty(values):
    result = []

    for value in values:
        value = clean(value)

        if value and value not in result:
            result.append(value)

    return result


# ============================================================
# DESTINATION CLASSIFICATION
# ============================================================

PACIFIC_KEYWORDS = [
    "pacific",
    "hawaii",
    "honolulu",
    "alaska",
    "vancouver",
    "mexico",
    "ensenada",
    "cabo san lucas",
    "cabo",
    "san diego",
    "los angeles",
    "long beach",
    "transpacific",
]


HOLIDAY_KEYWORDS = [
    "holiday",
    "halloween",
    "christmas",
    "thanksgiving",
    "merrytime",
    "very merrytime",
    "halloween on the high seas",
    "new year's",
    "new year",
]


def is_pacific(product):
    """
    Determine whether a cruise product is associated
    with a Pacific destination.

    Uses destination/geoArea/product text.
    """

    values = [
        product.get("productName", ""),
        product.get("productDisplayName", ""),
    ]

    for itinerary in product.get("itineraries", []):
        values.extend([
            itinerary.get("itineraryId", ""),
        ])

        for sailing in itinerary.get("sailings", []):
            values.extend([
                sailing.get("destination", ""),
                sailing.get("geoArea", ""),
            ])

    searchable = " ".join(
        clean(x).lower()
        for x in values
    )

    return any(
        keyword in searchable
        for keyword in PACIFIC_KEYWORDS
    )


def is_holiday(product):
    """
    Determine whether the cruise product is a holiday cruise.
    """

    searchable = recursive_text(product).lower()

    return any(
        keyword in searchable
        for keyword in HOLIDAY_KEYWORDS
    )


# ============================================================
# DEPARTURE PORT
# ============================================================

def extract_departure_port(product):
    """
    The available-products response does not expose a dedicated
    departurePort field.

    Therefore we first inspect the cruise-card/product names,
    which commonly contain patterns such as:

        "Cruise from Miami"
        "Cruise from London"

    Returns Miami, London, or blank.
    """

    names = [
        product.get("productDisplayName", ""),
        product.get("productName", ""),
    ]

    for name in names:

        name = clean(name)

        if not name:
            continue

        match = re.search(
            r"\bfrom\s+(Miami|London)\b",
            name,
            flags=re.IGNORECASE,
        )

        if match:
            return match.group(1).title()

    return ""


def is_miami_or_london_departure(product):
    port = extract_departure_port(product)

    return port in {"Miami", "London"}


# ============================================================
# NUMBER OF BOOKING DATES
# ============================================================

def get_number_of_sailings(product):
    """
    Disney exposes numberOfSailings on the itinerary object.
    This corresponds to the number of available sailing dates
    represented by that itinerary/product.
    """

    total = 0

    for itinerary in product.get("itineraries", []):

        value = itinerary.get(
            "numberOfSailings",
            0,
        )

        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0

        total += value

    return total


# ============================================================
# FLATTEN PRODUCT DATA
# ============================================================

def flatten_products(all_pages):

    records = []

    seen_sailings = set()

    for page_number, page_data in all_pages.items():

        for product in page_data.get(
            "products",
            [],
        ):

            product_id = clean(
                product.get("productId")
            )

            product_name = clean(
                product.get("productName")
            )

            display_name = clean(
                product.get("productDisplayName")
            )

            departure_port = extract_departure_port(
                product
            )

            pacific = is_pacific(product)

            holiday = is_holiday(product)

            booking_dates = get_number_of_sailings(
                product
            )

            itineraries = product.get(
                "itineraries",
                [],
            )

            # ------------------------------------------------
            # Preserve product-level information even if
            # no sailing records exist.
            # ------------------------------------------------

            if not itineraries:

                records.append({
                    "api_page": page_number,
                    "product_id": product_id,
                    "product_name": product_name,
                    "product_display_name": display_name,
                    "sailing_id": "",
                    "package_id": "",
                    "package_code": "",
                    "ship": "",
                    "destination": "",
                    "geo_area": "",
                    "number_of_nights": "",
                    "number_of_sailings": booking_dates,
                    "departure_port": departure_port,
                    "is_pacific": pacific,
                    "is_holiday": holiday,
                    "raw_product": safe_json(product),
                    "raw_itinerary": "",
                    "raw_sailing": "",
                })

                continue

            # ------------------------------------------------
            # Flatten every sailing.
            # ------------------------------------------------

            for itinerary in itineraries:

                itinerary_sailings = itinerary.get(
                    "sailings",
                    [],
                )

                if not itinerary_sailings:

                    records.append({
                        "api_page": page_number,
                        "product_id": product_id,
                        "product_name": product_name,
                        "product_display_name": display_name,
                        "sailing_id": "",
                        "package_id": "",
                        "package_code": "",
                        "ship": "",
                        "destination": "",
                        "geo_area": "",
                        "number_of_nights": "",
                        "number_of_sailings": booking_dates,
                        "departure_port": departure_port,
                        "is_pacific": pacific,
                        "is_holiday": holiday,
                        "raw_product": safe_json(product),
                        "raw_itinerary": safe_json(
                            itinerary
                        ),
                        "raw_sailing": "",
                    })

                    continue

                for sailing in itinerary_sailings:

                    sailing_id = clean(
                        sailing.get(
                            "sailingId"
                        )
                    )

                    unique_key = (
                        product_id,
                        sailing_id,
                    )

                    if unique_key in seen_sailings:
                        continue

                    seen_sailings.add(
                        unique_key
                    )

                    ship = sailing.get(
                        "ship",
                        {}
                    )

                    ship_name = clean(
                        ship.get("name")
                        if isinstance(ship, dict)
                        else ""
                    )

                    records.append({
                        "api_page": page_number,

                        "product_id": product_id,

                        "product_name": product_name,

                        "product_display_name":
                            display_name,

                        "sailing_id":
                            sailing_id,

                        "package_id":
                            clean(
                                sailing.get(
                                    "packageId"
                                )
                            ),

                        "package_code":
                            clean(
                                sailing.get(
                                    "packageCode"
                                )
                            ),

                        "ship":
                            ship_name,

                        "destination":
                            clean(
                                sailing.get(
                                    "destination"
                                )
                            ),

                        "geo_area":
                            clean(
                                sailing.get(
                                    "geoArea"
                                )
                            ),

                        "number_of_nights":
                            clean(
                                sailing.get(
                                    "numberOfNights"
                                )
                            ),

                        "number_of_sailings":
                            booking_dates,

                        "departure_port":
                            departure_port,

                        "is_pacific":
                            pacific,

                        "is_holiday":
                            holiday,

                        # Complete raw sections required
                        # for auditability.
                        "raw_product":
                            safe_json(product),

                        "raw_itinerary":
                            safe_json(itinerary),

                        "raw_sailing":
                            safe_json(sailing),
                    })

    return records


# ============================================================
# MAIN SCRAPER
# ============================================================

def main():

    print()
    print("=" * 65)
    print("DISNEY CRUISE — OBJECTIVE 1")
    print("=" * 65)

    with sync_playwright() as p:

        print("\nConnecting to authenticated Chrome...")

        browser = p.chromium.connect_over_cdp(
            CDP_URL
        )

        context = browser.contexts[0]

        page = None

        for tab in context.pages:

            if (
                "disneycruise.disney.go.com"
                in tab.url
            ):
                page = tab
                break

        if page is None:

            page = context.pages[0]

            page.goto(
                DISNEY_LIST_URL,
                wait_until="domcontentloaded",
                timeout=120000,
            )

        print("Page:", page.url)

        # ----------------------------------------------------
        # PAGE 1
        # ----------------------------------------------------

        print("\nFetching page 1...")

        with page.expect_response(
            lambda response:
                "/available-products/"
                in response.url
                and response.request.method
                == "POST"
                and response.status == 200,
            timeout=120000,
        ) as response_info:

            page.reload(
                wait_until="domcontentloaded",
                timeout=120000,
            )

        response = response_info.value

        page_one = response.json()

        total_pages = int(
            page_one.get(
                "totalPages",
                0,
            )
        )

        total_available = int(
            page_one.get(
                "totalAvailableCruises",
                0,
            )
        )

        print(
            "Total API pages:",
            total_pages,
        )

        print(
            "Disney reported cruises:",
            total_available,
        )

        all_pages = {
            "metadata": {
                "totalPages": total_pages,
                "totalAvailableCruises":
                    total_available,
                "extractionMethod":
                    "Authenticated browser API",
                "endpoint":
                    API_URL,
            },
            "pages": {
                "1": page_one,
            },
        }

        # ----------------------------------------------------
        # PAGES 2 -> N
        # ----------------------------------------------------

        for page_number in range(
            2,
            total_pages + 1,
        ):

            payload = dict(
                BASE_PAYLOAD
            )

            payload["page"] = page_number

            print(
                f"Fetching page "
                f"{page_number}/{total_pages}..."
            )

            result = page.evaluate(
                """
                async ({url, payload}) => {

                    const response =
                        await fetch(
                            url,
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type":
                                        "application/json"
                                },
                                body:
                                    JSON.stringify(
                                        payload
                                    )
                            }
                        );

                    if (!response.ok) {
                        throw new Error(
                            "HTTP " +
                            response.status
                        );
                    }

                    return await response.json();
                }
                """,
                {
                    "url": API_URL,
                    "payload": payload,
                },
            )

            products_count = len(
                result.get(
                    "products",
                    [],
                )
            )

            print(
                "  products:",
                products_count,
            )

            all_pages["pages"][
                str(page_number)
            ] = result

            time.sleep(0.5)

        # ----------------------------------------------------
        # SAVE COMPLETE RAW JSON
        # ----------------------------------------------------

        RAW_JSON_FILE.write_text(
            json.dumps(
                all_pages,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(
            "\nRaw JSON saved:",
            RAW_JSON_FILE.resolve(),
        )

        # ----------------------------------------------------
        # FLATTEN
        # ----------------------------------------------------

        records = flatten_products(
            all_pages["pages"]
        )

        df = pd.DataFrame(records)

        # ----------------------------------------------------
        # CLEAN
        # ----------------------------------------------------

        critical_columns = [
            "product_id",
            "product_name",
        ]

        for column in critical_columns:

            df[column] = (
                df[column]
                .fillna("")
                .astype(str)
                .str.strip()
            )

        df = df[
            (df["product_id"] != "")
            &
            (df["product_name"] != "")
        ]

        # Remove duplicate product+sailing rows.
        df = df.drop_duplicates(
            subset=[
                "product_id",
                "sailing_id",
            ],
            keep="first",
        )

        # ----------------------------------------------------
        # SAVE CSV
        # ----------------------------------------------------

        df.to_csv(
            CSV_FILE,
            index=False,
            encoding="utf-8-sig",
        )

        print(
            "CSV saved:",
            CSV_FILE.resolve(),
        )

        # ----------------------------------------------------
        # PRODUCT-LEVEL ANALYSIS
        # ----------------------------------------------------

        product_rows = {}

        for page_data in all_pages["pages"].values():

            for product in page_data.get(
                "products",
                [],
            ):

                product_id = product.get(
                    "productId"
                )

                if product_id:
                    product_rows[
                        product_id
                    ] = product

        unique_products = list(
            product_rows.values()
        )

        # ----------------------------------------------------
        # ANSWER 1
        # Pacific destinations
        # ----------------------------------------------------

        pacific_count = sum(
            1
            for product in unique_products
            if is_pacific(product)
        )

        # ----------------------------------------------------
        # ANSWER 2
        # Total cruises
        # ----------------------------------------------------

        total_cruises = total_available

        # ----------------------------------------------------
        # ANSWER 3
        # Holiday cruises
        # ----------------------------------------------------

        holiday_count = sum(
            1
            for product in unique_products
            if is_holiday(product)
        )

        # ----------------------------------------------------
        # ANSWER 4
        # More than 2 booking dates
        # ----------------------------------------------------

        more_than_two = sum(
            1
            for product in unique_products
            if get_number_of_sailings(
                product
            ) > 2
        )

        # ----------------------------------------------------
        # ANSWER 5
        # Miami + London departure ports
        # ----------------------------------------------------

        miami_count = 0
        london_count = 0

        for product in unique_products:

            port = extract_departure_port(
                product
            )

            if port == "Miami":
                miami_count += 1

            elif port == "London":
                london_count += 1

        miami_london_count = (
            miami_count +
            london_count
        )

        # ----------------------------------------------------
        # WRITE ANSWERS FILE
        # ----------------------------------------------------

        answer_text = f"""
DISNEY CRUISE — OBJECTIVE 1 RESULTS
====================================

Extraction
----------
API pages fetched              : {total_pages}
API reported total cruises     : {total_cruises}
Unique cruise products         : {len(unique_products)}
Final CSV rows                 : {len(df)}

Challenge Answers
-----------------
1. Pacific destination cruises : {pacific_count}
2. Total cruises               : {total_cruises}
3. Holiday cruises             : {holiday_count}
4. Cruises with >2 dates       : {more_than_two}
5. Miami + London departures   : {miami_london_count}

Departure Breakdown
-------------------
Miami departures               : {miami_count}
London departures              : {london_count}

Files
-----
Raw JSON                       : {RAW_JSON_FILE.name}
Final CSV                      : {CSV_FILE.name}
Answers                        : {ANSWERS_FILE.name}
""".strip()

        ANSWERS_FILE.write_text(
            answer_text,
            encoding="utf-8",
        )

        # ----------------------------------------------------
        # FINAL TERMINAL OUTPUT
        # ----------------------------------------------------

        print()
        print("=" * 65)
        print("DISNEY CRUISE — FINAL RESULTS")
        print("=" * 65)

        print()
        print("EXTRACTION")
        print("-" * 65)

        print(
            f"API pages fetched              : "
            f"{total_pages}"
        )

        print(
            f"API reported total cruises     : "
            f"{total_cruises}"
        )

        print(
            f"Unique cruise products         : "
            f"{len(unique_products)}"
        )

        print(
            f"Final CSV rows                 : "
            f"{len(df)}"
        )

        print()
        print("CHALLENGE ANSWERS")
        print("-" * 65)

        print(
            f"1. Pacific destination cruises : "
            f"{pacific_count}"
        )

        print(
            f"2. Total cruises               : "
            f"{total_cruises}"
        )

        print(
            f"3. Holiday cruises             : "
            f"{holiday_count}"
        )

        print(
            f"4. Cruises with >2 dates       : "
            f"{more_than_two}"
        )

        print(
            f"5. Miami + London departures   : "
            f"{miami_london_count}"
        )

        print()
        print("DEPARTURE BREAKDOWN")
        print("-" * 65)

        print(
            f"Miami                          : "
            f"{miami_count}"
        )

        print(
            f"London                         : "
            f"{london_count}"
        )

        print()
        print("FILES")
        print("-" * 65)

        print(
            f"Raw JSON                       : "
            f"{RAW_JSON_FILE.name}"
        )

        print(
            f"Final CSV                      : "
            f"{CSV_FILE.name}"
        )

        print(
            f"Answers                        : "
            f"{ANSWERS_FILE.name}"
        )

        print()
        print("=" * 65)
        print("OBJECTIVE 1 COMPLETE")
        print("=" * 65)

        browser.close()


if __name__ == "__main__":
    main()