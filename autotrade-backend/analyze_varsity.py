import os
import glob
from bs4 import BeautifulSoup
import PyPDF2

base_dir = "/home/cis/Downloads/Zerodha varsity"

print("Starting analysis of Varsity materials...")

# Just a quick check of what exists
pdfs = glob.glob(f"{base_dir}/*.pdf")
inner = glob.glob(f"{base_dir}/innerworth_articles/*.html")
sector = glob.glob(f"{base_dir}/sector_analysis_articles/*.html")

print(f"Found {len(pdfs)} Modules, {len(inner)} Innerworth articles, {len(sector)} Sector Analysis articles.")
