# Relu Consultancy – Data Extraction Engineer Hiring Challenge

## Overview

This repository contains my implementation for the Relu Consultancy Data Extraction Engineer Hiring Challenge.

The solution covers both mandatory data extraction objectives using Python, Playwright, API/network inspection, JSON processing, and CSV generation.

---

## Objective 1 – Disney Cruise

Target:
https://disneycruise.disney.go.com/en-in/

### Implementation

The Disney extraction uses the site's underlying availability API discovered through browser network inspection.

The scraper:

- Fetches all 35 available API pages
- Extracts cruise/product and itinerary information
- Flattens the nested API response into CSV records
- Removes duplicate records
- Preserves the raw API responses
- Generates the required challenge answers

### Results

- API pages fetched: 35
- API reported total cruises: 948
- Unique cruise products: 110
- Final CSV records: 323

See:

`Disney/disney_objective1_answers.txt`

---

## Objective 2 – Ingredients Network

Target:
https://www.ingredientsnetwork.com/

### Implementation

The scraper:

- Discovers company profile URLs
- Extracts company profile information
- Uses concurrent Playwright pages for faster extraction
- Supports checkpoint/resume
- Saves the extracted records to CSV
- Tracks failed/missing profiles

### Results

- Company URLs discovered: 291
- Company profiles successfully scraped: 291
- Failed profiles: 0
- Final CSV records: 291

See:

`IngredientsNetwork/ingredients_objective2_answers.txt`

---

## Technologies

- Python
- Playwright
- Pandas
- JSON
- CSV
- REST/API extraction
- Browser automation
- Concurrent scraping
- Checkpoint/resume processing

---

## Repository Structure

### Disney

- `disney_cruises_final.csv` – final extracted dataset
- `disney_all_pages.json` – raw API responses
- `disney_objective1_answers.txt` – challenge answers
- `objective1_final_scrapper_code.py` – final scraper

### Ingredients Network

- `ingredients_network_final.csv` – final extracted dataset
- `ingredients_network_links.json` – discovered company URLs
- `ingredients_network_checkpoint.json` – scraper checkpoint
- `ingredients_objective2_answers.txt` – challenge answers

### Archive

The `archive/` directory contains intermediate scripts, debugging files, captured responses, and development artifacts generated during the extraction process.

---

## Reproducibility

The final scraper implementations and extracted datasets are included in the repository.

The `.venv` environment is intentionally excluded from the submission.