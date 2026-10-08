# SerpApi test cases: is the app's Local Pack / Local Finder data correct?

Each test compares what the app gets from SerpApi with what Google shows in **your own browser, sent the exact same search and location**.

## How to run a test
1. **Get the app's result.** On the project page → Rankings → **Run ranking check**, choose the **Search from** shown in the test and run it. The keyword must be switched **On** in step 2. A test marked **Expected** already has a stored result, so you don't need to spend credits.
2. **Get Google's result in a clean browser:**
   - Open an **incognito / private window** and don't log in to Google.
   - Block location for google.com: click the padlock → Location → **Block**.
   - For **phone** tests (the app checks as a phone), press **F12**, then **Ctrl+Shift+M** (phone view), and reload.
3. **Open the links** in the test:
   - **Local Pack**: the map box with 3 businesses at the top of the results.
   - **Local Finder**: the “More places” / Places list (Google's own ranking for the Local Pack).
   - **Maps**: the Google Maps list. This is what the app's “Local Finder” column uses for City centre and Business location.
4. **Compare**, ignoring anything marked **Sponsored**.

## Pass / fail rule
| Result | Meaning |
|---|---|
| ✅ **Pass** | Same businesses in the Local Pack top 3, and the client's position within **±1** of the app's |
| 🟡 **Pass with note** | Same top 3 but in a different order, or a different #3. Normal: results move hour to hour, and phone and computer differ |
| ❌ **Fail** | Mostly different businesses, or the client missing in one view but #1–2 in the other. Recheck that the browser isn't using your real location (no “India” or other-country results) |

## What the app sends to SerpApi
| Search from | Local Pack | “Local Finder” column |
|---|---|---|
| City centre | `engine=google`, mobile, `uule` = city centre point (5 km) | `engine=google_maps`, `ll=@city centre,14z` |
| Business location | `engine=google`, mobile, `uule` = business pin (5 km) | `engine=google_maps`, `ll=@business pin,14z` |
| Whole country | `engine=google`, mobile, `location=<country>` | `engine=google_local` (Google's Local Finder), `location=<country>` |

All searches use `hl=en` and `gl` = the country of the business. Sponsored results are never counted.

---

## T1 · OVO Painting · “cabinet painting in Atlanta” · City centre

**Tests:** City name in the words **and** searcher placed in the city centre (location twice): the most stable case.

- **App setting:** Search from = **City centre**, keyword **cabinet painting in Atlanta**, device **phone**
- **Searcher placed at:** Atlanta, GA city centre (33.7501, -84.3885)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/cabinet+painting+in+Atlanta/@33.7501,-84.3885,14z?hl=en&gl=us)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T2 · OVO Painting · “cabinet painting near me” · City centre

**Tests:** No place in the words: Google relies only on the searcher's point.

- **App setting:** Search from = **City centre**, keyword **cabinet painting near me**, device **phone**
- **Searcher placed at:** Atlanta, GA city centre (33.7501, -84.3885)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+near+me&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+near+me&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/cabinet+painting+near+me/@33.7501,-84.3885,14z?hl=en&gl=us)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T3 · OVO Painting · “cabinet painting Atlanta” · City centre

**Tests:** City name without “in”.

- **App setting:** Search from = **City centre**, keyword **cabinet painting Atlanta**, device **phone**
- **Searcher placed at:** Atlanta, GA city centre (33.7501, -84.3885)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzNzUwMTAwMApsb25naXR1ZGVfZTc6LTg0Mzg4NTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/cabinet+painting+Atlanta/@33.7501,-84.3885,14z?hl=en&gl=us)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T4 · OVO Painting · “cabinet painting in Atlanta” · Business location

**Tests:** Searcher at the business's own address (16 km from downtown): compare with T1 to see the distance effect.

- **App setting:** Search from = **Business location**, keyword **cabinet painting in Atlanta**, device **phone**
- **Searcher placed at:** business location (33.8933, -84.3819)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzODkzMzAwMApsb25naXR1ZGVfZTc6LTg0MzgxOTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjMzODkzMzAwMApsb25naXR1ZGVfZTc6LTg0MzgxOTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/cabinet+painting+in+Atlanta/@33.8933,-84.3819,14z?hl=en&gl=us)
- **Expected (stored app data):** Local Pack: 1 JB Cabinet Painting & Wood Shop · 2 **OVO Painting** · 3 Creative Cabinets and Fine Finishes. Maps: 1 Full Coverage Painting and Flooring · 2 Five Star Painting of Atlanta · 3 Intown Painting … 7 **OVO Painting**

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T5 · OVO Painting · “cabinet painting in Atlanta” · Whole country

**Tests:** Whole country (United States): no exact point, Google uses the city in the words.

- **App setting:** Search from = **Whole country**, keyword **cabinet painting in Atlanta**, device **phone**
- **Searcher placed at:** United States (whole country)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=w%2BCAIQICINVW5pdGVkIFN0YXRlcw%3D%3D)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+in+Atlanta&gl=us&hl=en&uule=w%2BCAIQICINVW5pdGVkIFN0YXRlcw%3D%3D&tbm=lcl)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T6 · OVO Painting · “cabinet painting near me” · Whole country

**Tests:** Edge case: “near me” with no point. Expect a weak or missing Local Pack: this keyword only makes sense with City centre or Business location.

- **App setting:** Search from = **Whole country**, keyword **cabinet painting near me**, device **phone**
- **Searcher placed at:** United States (whole country)
- **Local Pack link:** [Open](https://www.google.com/search?q=cabinet+painting+near+me&gl=us&hl=en&uule=w%2BCAIQICINVW5pdGVkIFN0YXRlcw%3D%3D)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=cabinet+painting+near+me&gl=us&hl=en&uule=w%2BCAIQICINVW5pdGVkIFN0YXRlcw%3D%3D&tbm=lcl)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T7 · Durahome Painting Plus · “exterior house painting in Saint Paul” · Business location

**Tests:** Google shows **no Local Pack** for this search: the app must say “no Local Pack shown”, not “not found”.

- **App setting:** Search from = **Business location**, keyword **exterior house painting in Saint Paul**, device **phone**
- **Searcher placed at:** business location (44.9565, -93.1821)
- **Local Pack link:** [Open](https://www.google.com/search?q=exterior+house+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=exterior+house+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/exterior+house+painting+in+Saint+Paul/@44.9565,-93.1821,14z?hl=en&gl=us)
- **Expected (stored app data):** Local Pack: none shown. Maps: 1 **Durahome Painting Plus**

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T8 · Durahome Painting Plus · “exterior painting in Saint Paul” · Business location

**Tests:** Re-tested on 9 Oct through SerpApi: identical to the 8 Oct result (repeatable).

- **App setting:** Search from = **Business location**, keyword **exterior painting in Saint Paul**, device **phone**
- **Searcher placed at:** business location (44.9565, -93.1821)
- **Local Pack link:** [Open](https://www.google.com/search?q=exterior+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=exterior+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/exterior+painting+in+Saint+Paul/@44.9565,-93.1821,14z?hl=en&gl=us)
- **Expected (stored app data):** Local Pack: 1 **Durahome Painting Plus** · 2 Cutting Edge Painting LLC · 3 Revive House Painting LLP. Maps: 1 **Durahome** · 2 Mac Grove Painting · 3 Cutting Edge Painting LLC

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T9 · Durahome Painting Plus · “exterior painting in Saint Paul” · Business location · computer

**Tests:** Same as T8 on a **computer**: Google adds **Sponsored** ads here (4 in the 9 Oct test). The app must ignore them. Without ads, #2 and #3 may swap.

- **App setting:** Search from = **Business location**, keyword **exterior painting in Saint Paul**, device **computer (compare by hand only: the app always checks as a phone)**
- **Searcher placed at:** business location (44.9565, -93.1821)
- **Local Pack link:** [Open](https://www.google.com/search?q=exterior+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=exterior+painting+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTU2NTAwMApsb25naXR1ZGVfZTc6LTkzMTgyMTAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/exterior+painting+in+Saint+Paul/@44.9565,-93.1821,14z?hl=en&gl=us)
- **Expected (stored app data):** Local Pack (ads ignored): 1 **Durahome** · 2 Revive House Painting LLP · 3 Cutting Edge Painting LLC

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T10 · Durahome Painting Plus · “painter in Saint Paul” · City centre

**Tests:** City centre of Saint Paul (downtown, 9 km east of Durahome).

- **App setting:** Search from = **City centre**, keyword **painter in Saint Paul**, device **phone**
- **Searcher placed at:** Saint Paul, MN city centre (44.9537, -93.09)
- **Local Pack link:** [Open](https://www.google.com/search?q=painter+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTUzNzAwMApsb25naXR1ZGVfZTc6LTkzMDkwMDAwMAp9CnJhZGl1czo1MDAw)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=painter+in+Saint+Paul&gl=us&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3OjQ0OTUzNzAwMApsb25naXR1ZGVfZTc6LTkzMDkwMDAwMAp9CnJhZGl1czo1MDAw&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/painter+in+Saint+Paul/@44.9537,-93.09,14z?hl=en&gl=us)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T11 · Proximity Plumbing · “blocked drains near me” · Business location

**Tests:** Big Local Pack vs Maps difference (Maps favours names containing “drains”).

- **App setting:** Search from = **Business location**, keyword **blocked drains near me**, device **phone**
- **Searcher placed at:** business location (-33.8683, 151.2514)
- **Local Pack link:** [Open](https://www.google.com/search?q=blocked+drains+near+me&gl=au&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3Oi0zMzg2ODMwMDAKbG9uZ2l0dWRlX2U3OjE1MTI1MTQwMDAKfQpyYWRpdXM6NTAwMA%3D%3D)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=blocked+drains+near+me&gl=au&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3Oi0zMzg2ODMwMDAKbG9uZ2l0dWRlX2U3OjE1MTI1MTQwMDAKfQpyYWRpdXM6NTAwMA%3D%3D&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/blocked+drains+near+me/@-33.8683,151.2514,14z?hl=en&gl=au)
- **Expected (stored app data):** Local Pack: 1 **Proximity Plumbing** · 2 Vaucluse Blocked Drains · 3 Sydney Blocked Drains. Maps: 1 Sydney Blocked Drains · 2 Emergency Drains Sydney · 3 Sewer Surgeon … 17 **Proximity Plumbing**

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T12 · Proximity Plumbing · “plumber in Point Piper” · Whole country

**Tests:** Whole country (Australia), other country/language settings (gl=au).

- **App setting:** Search from = **Whole country**, keyword **plumber in Point Piper**, device **phone**
- **Searcher placed at:** Australia (whole country)
- **Local Pack link:** [Open](https://www.google.com/search?q=plumber+in+Point+Piper&gl=au&hl=en&uule=w%2BCAIQICIJQXVzdHJhbGlh)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=plumber+in+Point+Piper&gl=au&hl=en&uule=w%2BCAIQICIJQXVzdHJhbGlh&tbm=lcl)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

## T13 · Proximity Plumbing · “plumber near me” · City centre

**Tests:** Small suburb centre (Point Piper): checks suburb-level points work.

- **App setting:** Search from = **City centre**, keyword **plumber near me**, device **phone**
- **Searcher placed at:** Point Piper, NSW city centre (-33.8671, 151.2523)
- **Local Pack link:** [Open](https://www.google.com/search?q=plumber+near+me&gl=au&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3Oi0zMzg2NzEwMDAKbG9uZ2l0dWRlX2U3OjE1MTI1MjMwMDAKfQpyYWRpdXM6NTAwMA%3D%3D)
- **Local Finder link (“More places”):** [Open](https://www.google.com/search?q=plumber+near+me&gl=au&hl=en&uule=a%2Bcm9sZToxCnByb2R1Y2VyOjEyCnByb3ZlbmFuY2U6Ngp0aW1lc3RhbXA6MTcwMDAwMDAwMDAwMDAwMApsYXRsbmd7CmxhdGl0dWRlX2U3Oi0zMzg2NzEwMDAKbG9uZ2l0dWRlX2U3OjE1MTI1MjMwMDAKfQpyYWRpdXM6NTAwMA%3D%3D&tbm=lcl)
- **Google Maps link** (the app's 2nd column): [Open](https://www.google.com/maps/search/plumber+near+me/@-33.8671,151.2523,14z?hl=en&gl=au)
- **Expected:** run the check with this Search from, then compare with the links

| | App (SerpApi) | Your browser | Pass? |
|---|---|---|---|
| Local Pack #1 / #2 / #3 | | | |
| Client's Local Pack position | | | |
| Client's Maps / Local Finder position | | | |

---

## Why small differences are normal
- **Time:** Google re-ranks local results during the day; a check made hours earlier can differ by a place or two.
- **Phone vs computer:** the app checks as a phone; links opened on a computer show the desktop layout.
- **Your browser's location:** if anything from your own city or country appears (e.g. “Ghaziabad, Uttar Pradesh”), Google is still using your real location; block location, use incognito, or add a VPN to that country.
- **Ads:** Sponsored listings appear at random and are always ignored by the app.
- **Maps vs Local Finder:** the Google Maps list ranks differently from the “More places” list. For City centre and Business location the app's 2nd column is Maps; use the Maps link for that column.
