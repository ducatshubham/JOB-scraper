import asyncio
import json
import csv
import os
import time
import sys
import subprocess
import random
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, urljoin, urlencode, parse_qs
from playwright.async_api import async_playwright

# -----------------------
# File paths
# -----------------------
cookies_path = Path("cookies_recruiter.json")

# Constants to reduce duplication
SCROLL_HEIGHT_JS = "document.body.scrollHeight"
SCROLL_BY_JS = "window.scrollBy(0, {})"
SCROLL_TO_TOP_JS = "window.scrollTo(0, 0)"
SCROLL_TO_BOTTOM_JS = "window.scrollTo(0, document.body.scrollHeight)"

# -----------------------
# Helpers for Recruiter
# -----------------------
def extract_role_from_recruiter_url(search_url):
    """Extract role/job title from LinkedIn Recruiter search URL"""
    try:
        parsed_url = urlparse(search_url)
        query_params = parse_qs(parsed_url.query)
        
        # Recruiter uses 'searchKeyword' parameter
        search_keyword = query_params.get('searchKeyword', [''])[0]
        if search_keyword:
            # Clean and format the role name
            role = search_keyword.replace('%22', '').replace('"', '').replace('%20', ' ').replace('%2520', ' ').strip()
            return role.title() if role else "Professional"
        return "Professional"
    except Exception:
        return "Professional"

def validate_recruiter_url(url):
    """Validate if URL is a LinkedIn Recruiter search URL"""
    return "linkedin.com/talent/search" in url

def ask_question(prompt_text: str) -> str:
    return input(prompt_text)

async def delay(ms: int):
    await asyncio.sleep(ms / 1000)

def initialize_csv(role_name):
    """Initialize CSV file with headers and return the file path"""
    role_clean = re.sub(r'[^\w\s-]', '', role_name).strip()
    role_clean = re.sub(r'[-\s]+', '_', role_clean)
    output_file = Path(f"linkedin_recruiter_{role_clean.lower()}_results.csv")
    
    headers = [
        "Name", "Title", "Location", "Education", "Profile URL",
        "Total Experience", "Experience Details", "Skills"
    ]
    
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
    
    print(f"✅ CSV file initialized: {output_file}")
    return output_file

def append_to_csv(output_file, profile_data):
    """Append a single profile to the CSV file immediately"""
    headers = [
        "Name", "Title", "Location", "Education", "Profile URL",
        "Total Experience", "Experience Details", "Skills"
    ]
    
    with open(output_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerow({
            "Name": profile_data.get("name", "N/A"),
            "Title": profile_data.get("title", "N/A"),
            "Location": profile_data.get("location", "N/A"),
            "Education": profile_data.get("education", "N/A"),
            "Profile URL": profile_data.get("url", ""),
            "Total Experience": profile_data.get("total_experience", "N/A"),
            "Experience Details": profile_data.get("experience_details", "N/A"),
            "Skills": profile_data.get("skills", "N/A")
        })
    print(f"💾 Saved to CSV: {profile_data.get('name', 'N/A')}")

def open_excel(file_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])
        else:
            subprocess.run(["xdg-open", file_path])
        print("📊 Opened Excel file.")
    except Exception as e:
        print(f"❌ Could not open Excel: {e}")

async def auto_scroll(page, step=600, max_rounds=30, wait_ms=1500):
    """Slow incremental scroll to trigger lazy-load."""
    try:
        last_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            await page.evaluate(SCROLL_BY_JS.format(step))
            await page.wait_for_timeout(wait_ms)
            new_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
            if new_height == last_height:
                await page.evaluate("window.scrollBy(0, 50);")
                await page.wait_for_timeout(wait_ms)
                new_height = await page.evaluate(f"() => {SCROLL_HEIGHT_JS}")
                if new_height == last_height:
                    break
            last_height = new_height
        print("ℹ Scrolled page to load dynamic content.")
    except Exception as e:
        print(f"❌ Failed to scroll: {e}")

def clean_recruiter_profile_url(u: str) -> str:
    """Clean recruiter profile URL - keep search context"""
    try:
        if "/talent/profile/" in u:
            return u
        return u
    except Exception:
        return u

async def detect_profile_type_from_page(page):
    """Automatically detect what type of profiles are being searched"""
    try:
        await page.wait_for_timeout(3000)
        
        detected_role = await page.evaluate(r"""() => {
            const searchInputs = document.querySelectorAll('input[type="search"], input[placeholder*="search"]');
            for (const input of searchInputs) {
                if (input.value && input.value.trim()) {
                    return input.value.trim();
                }
            }
            
            const titleElements = document.querySelectorAll(
                '[data-test-row-lockup-headline], ' +
                'span[data-live-test-row-lockup-headline], ' +
                '.artdeco-entity-lockup__subtitle, ' +
                'em.sh'
            );
            
            const titleCounts = {};
            for (const el of titleElements) {
                const text = el.textContent.trim();
                let jobTitle = text.split('@')[0].trim();
                jobTitle = jobTitle.replace(/\([^)]*\)/g, '').trim();
                
                const normalizedTitle = jobTitle.toLowerCase().replace(/[^a-z\s]/g, '');
                if (normalizedTitle.length > 5) {
                    titleCounts[normalizedTitle] = (titleCounts[normalizedTitle] || 0) + 1;
                }
            }
            
            let mostCommonTitle = '';
            let maxCount = 0;
            for (const [title, count] of Object.entries(titleCounts)) {
                if (count > maxCount) {
                    maxCount = count;
                    mostCommonTitle = title;
                }
            }
            
            if (mostCommonTitle) {
                const words = mostCommonTitle.split(/\s+/);
                const stopWords = ['the', 'a', 'an', 'and', 'or', 'but', 'at', 'to', 'for'];
                const keyWords = words.filter(w => !stopWords.includes(w) && w.length > 2);
                
                if (keyWords.length > 0) {
                    return keyWords.slice(0, 3).join(' ');
                }
            }
            
            const pageTitle = document.title;
            const titleMatch = pageTitle.match(/(\w+[\s\w]*)\s*[-|]/);
            if (titleMatch && titleMatch[1]) {
                return titleMatch[1].trim();
            }
            
            return null;
        }""")
        
        if detected_role:
            detected_role = detected_role.strip().title()
            print(f"✨ Auto-detected profile type: {detected_role}")
            return detected_role
        else:
            print("⚠️ Could not auto-detect profile type, using default")
            return "Professional"
            
    except Exception as e:
        print(f"⚠️ Error detecting profile type: {e}")
        return "Professional"

async def setup_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--window-size=1920,1080",
            "--disable-dev-shm-usage"
        ]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768}
    )
    page = await context.new_page()

    if cookies_path.exists():
        try:
            cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
            await context.add_cookies(cookies)
            print("✅ Loaded recruiter cookies from file.")
        except Exception as e:
            print(f"❌ Failed to load cookies: {e}")

    try:
        print("🔄 Loading LinkedIn...")
        await page.goto("https://www.linkedin.com/feed/", timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)
        print("✅ LinkedIn loaded successfully.")
    except Exception as e:
        print(f"❌ Failed to load LinkedIn: {e}")

    current_url = page.url
    if "/login" in current_url or "challenge" in current_url or "/checkpoint" in current_url or "/uas/login" in current_url:
        print("👉 Please log in manually with your LinkedIn account in the opened browser window...")
        print("🔑 Make sure you have access to LinkedIn Recruiter!")
        print("🔑 After login, navigate to LinkedIn Recruiter or just stay on the feed page")
        ask_question("🔑 Press Enter after you've successfully logged in...")
        
        cookies = await context.cookies()
        cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print("💾 Login session saved!")
        
        await page.wait_for_timeout(5000)

    try:
        print("🔄 Testing LinkedIn Recruiter access...")
        await page.goto("https://www.linkedin.com/talent/home", timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(5000)
        
        current_url = page.url
        if "/uas/login" in current_url or "/login" in current_url:
            print("❌ LinkedIn Recruiter access denied. You may not have Recruiter permissions.")
            print("🔄 Continuing with regular LinkedIn access...")
            await page.goto("https://www.linkedin.com/feed/", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
        else:
            print("✅ LinkedIn Recruiter access confirmed!")
            
    except Exception as e:
        print(f"⚠️ Recruiter test failed: {e}")
        print("🔄 Falling back to regular LinkedIn...")
        try:
            await page.goto("https://www.linkedin.com/feed/", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
        except Exception as e2:
            print(f"❌ Failed to load LinkedIn: {e2}")

    return browser, context, page

async def get_public_profile_url(page):
    """Extract public LinkedIn profile URL from the recruiter profile page"""
    try:
        public_profile_url = await page.evaluate(r"""() => {
            const publicProfileBtn = document.querySelector('button[data-test-public-profile-trigger]');
            if (publicProfileBtn) {
                publicProfileBtn.click();
                return new Promise((resolve) => {
                    setTimeout(() => {
                        const copyLinkBtn = document.querySelector('button[data-test-copy-public-profile-link-btn]');
                        const linkElement = document.querySelector('a[href*="linkedin.com/in/"]');
                        
                        if (linkElement) {
                            resolve(linkElement.href);
                        } else if (copyLinkBtn) {
                            const url = copyLinkBtn.getAttribute('data-clipboard-text') || 
                                       copyLinkBtn.closest('[data-url]')?.getAttribute('data-url');
                            resolve(url);
                        } else {
                            resolve(null);
                        }
                    }, 1000);
                });
            }
            
            const directLink = document.querySelector('a[href*="linkedin.com/in/"]');
            if (directLink) {
                return directLink.href;
            }
            
            return null;
        }""")
        
        if public_profile_url:
            print(f"✅ Found public profile URL: {public_profile_url}")
            return public_profile_url
        else:
            print("⚠️ Could not find public profile URL")
            return None
            
    except Exception as e:
        print(f"❌ Error extracting public profile URL: {e}")
        return None

async def scrape_recruiter_education(page, profile_url):
    try:
        education_data = await page.evaluate(r"""() => {
            let education = "";
            
            const educationSelectors = [
                '.text-highlighter__text[data-test-text-highlighter-text-only]',
                'h2[data-test-expandable-list-title]:contains("Education") ~ * .text-highlighter__text',
                '.text-highlighter__text',
                '[data-test-education]',
                '.education-section',
                '.profile-education'
            ];
            
            const educationHeaders = document.querySelectorAll('h2[data-test-expandable-list-title]');
            for (const header of educationHeaders) {
                if (header.textContent && header.textContent.includes('Education')) {
                    let parent = header.parentElement;
                    if (parent) {
                        const textElements = parent.querySelectorAll('.text-highlighter__text[data-test-text-highlighter-text-only]');
                        for (const el of textElements) {
                            const text = el.textContent.trim();
                            if (text && (text.includes('University') || text.includes('College') || text.includes('Institute') || text.includes('School'))) {
                                education = text;
                                break;
                            }
                        }
                    }
                }
                if (education) break;
            }
            
            if (!education) {
                const textElements = document.querySelectorAll('.text-highlighter__text[data-test-text-highlighter-text-only]');
                for (const el of textElements) {
                    const text = el.textContent.trim();
                    if (text && (text.includes('University') || text.includes('College') || text.includes('Institute'))) {
                        education = text;
                        break;
                    }
                }
            }
            
            return education || "N/A";
        }""")

        return education_data if education_data and education_data != "N/A" else "Limited in Recruiter"

    except Exception as e:
        print(f"❌ Failed to scrape education for {profile_url}: {e}")
        return "N/A"

async def scrape_recruiter_skills(page, profile_url):
    try:
        # First, try to click "Show all skills" button if it exists
        try:
            show_more_button = await page.query_selector('button[data-test-expandable-button][aria-label*="Show all"]')
            if show_more_button and await show_more_button.is_visible():
                button_text = await show_more_button.inner_text()
                if 'skill' in button_text.lower():
                    print("🔍 Found 'Show all skills' button, clicking...")
                    await show_more_button.click()
                    await page.wait_for_timeout(2000)
                    print("✅ Expanded skills section")
        except Exception as e:
            print(f"⚠️ Could not click show more skills button: {e}")
        
        skills = await page.evaluate(r"""() => {
            const skillsList = [];
            
            // Primary selector based on your DOM structure
            const skillElements = document.querySelectorAll('dt.skill-entity__skill-name[data-test-skill-entity-skill-name], dt.skill-entity__skill-name[data-live-test-skill-entity-skill-name]');
            
            skillElements.forEach(el => {
                const skillText = el.textContent && el.textContent.trim();
                if (skillText && skillText.length > 1 && skillText.length < 100) {
                    skillsList.push(skillText);
                }
            });
            
            // Fallback: if no skills found, look for skills section and extract
            if (skillsList.length === 0) {
                const skillHeaders = document.querySelectorAll('h2[data-test-expandable-list-title]');
                for (const header of skillHeaders) {
                    if (header.textContent && header.textContent.includes('Skills')) {
                        let parent = header.closest('.expandable-list, .skills-card-expandable, section');
                        if (parent) {
                            const altSkillElements = parent.querySelectorAll('dt.skill-entity__skill-name, .skill-entity__skill-name');
                            altSkillElements.forEach(el => {
                                const skillText = el.textContent && el.textContent.trim();
                                if (skillText && skillText.length > 1 && skillText.length < 100) {
                                    skillsList.push(skillText);
                                }
                            });
                        }
                        break;
                    }
                }
            }
            
            return [...new Set(skillsList)];
        }""")

        if skills and len(skills) > 0:
            print(f"✅ Scraped {len(skills)} skills")
            return skills
        else:
            print("⚠️ No skills found, returning default")
            return ["Limited in Recruiter"]

    except Exception as e:
        print(f"❌ Failed to scrape skills for {profile_url}: {e}")
        return ["N/A"]

async def scrape_recruiter_experience(page, profile_url):
    try:
        # First, try to expand all experience by clicking "Show more" buttons
        try:
            print("🔍 Looking for 'Show more' buttons in Experience section...")
            await page.wait_for_timeout(5000)
            
            show_more_buttons = await page.query_selector_all(
                'button[aria-label*="Show all"], '
                'button[data-test-expandable-button], '
                'button:has-text("Show more"), '
                'button:has-text("Show all")'
            )
            
            for button in show_more_buttons:
                try:
                    if await button.is_visible():
                        button_text = await button.inner_text()
                        if 'experience' in button_text.lower() or 'show all' in button_text.lower():
                            print(f"✅ Clicking: {button_text}")
                            await button.click()
                            await page.wait_for_timeout(5000)
                except Exception:
                    continue
        except Exception as e:
            print(f"⚠️ Could not expand experience section: {e}")

        # Scroll to ensure all experience items are loaded
        print("📜 Scrolling to load all experience items...")
        await page.evaluate("window.scrollBy(0, 800);")
        await page.wait_for_timeout(4000)
        await page.evaluate("window.scrollBy(0, 800);")
        await page.wait_for_timeout(4000)

        experience_data = await page.evaluate(r"""() => {
            const experiences = [];
            let allDates = [];

            function parseDate(dateStr) {
                if (!dateStr) return null;
                dateStr = dateStr.trim().toLowerCase();
                if (dateStr.includes('present')) {
                    return new Date();
                }
                const monthMap = {
                    'jan': 0, 'feb': 1, 'mar': 2, 'apr': 3, 'may': 4, 'jun': 5,
                    'jul': 6, 'aug': 7, 'sep': 8, 'oct': 9, 'nov': 10, 'dec': 11,
                    'january': 0, 'february': 1, 'march': 2, 'april': 3, 'june': 5,
                    'july': 6, 'august': 7, 'september': 8, 'october': 9, 'november': 10, 'december': 11
                };
                const parts = dateStr.split(/[\s,]+/);
                let month = 0, year = null;
                for (const part of parts) {
                    if (monthMap[part] !== undefined) {
                        month = monthMap[part];
                    } else if (/^\d{4}$/.test(part)) {
                        year = parseInt(part);
                    }
                }
                if (year) {
                    return new Date(year, month, 1);
                }
                return null;
            }

            function calculateDuration(startDate, endDate) {
                if (!startDate) return "N/A";
                if (!endDate) endDate = new Date();
                const diffMs = endDate - startDate;
                const diffMonths = Math.floor(diffMs / (1000 * 60 * 60 * 24 * 30.44));
                const years = Math.floor(diffMonths / 12);
                const months = diffMonths % 12;
                if (years > 0 && months > 0) {
                    return `${years} yr${years > 1 ? 's' : ''} ${months} mo${months > 1 ? 's' : ''}`;
                } else if (years > 0) {
                    return `${years} yr${years > 1 ? 's' : ''}`;
                } else {
                    return `${months} mo${months > 1 ? 's' : ''}`;
                }
            }

            console.log('Starting experience extraction...');

            const companyHeaderSelectors = [
                '[data-test-company-card-heading]',
                '[data-live-test-company-card-heading]',
                'h3[data-test-company-card-heading]',
                'h3[data-live-test-company-card-heading]',
                'strong.grouped-position-entity__company-name'
            ];
            
            const companyHeaders = [];
            companyHeaderSelectors.forEach(selector => {
                document.querySelectorAll(selector).forEach(el => companyHeaders.push(el));
            });
            
            console.log(`Found ${companyHeaders.length} company headers`);
            
            companyHeaders.forEach((companyHeader, idx) => {
                try {
                    const companyName = companyHeader.textContent.trim();
                    console.log(`[${idx + 1}] Processing company: ${companyName}`);
                    
                    let companySection = companyHeader.closest('[data-test-group-position-list-container]') ||
                                        companyHeader.closest('[data-test-company-card]') || 
                                        companyHeader.closest('[data-live-test-company-card]') ||
                                        companyHeader.closest('.dSaeSjuHcErfVtiLoJXpFgNjQzAvqyXSIKTg') ||
                                        companyHeader.parentElement?.parentElement?.parentElement;
                    
                    if (!companySection) {
                        console.log(`  ⚠️ Could not find company section for ${companyName}`);
                        return;
                    }

                    let companyTotalDuration = "N/A";
                    const companyDurationEl = companySection.querySelector('[data-test-grouped-position-entity-date-overall-range], [data-live-test-grouped-position-entity-date-overall-range]');
                    if (companyDurationEl) {
                        companyTotalDuration = companyDurationEl.textContent.trim();
                        console.log(`  Company total duration: ${companyTotalDuration}`);
                    }
                    
                    const positionContainers = companySection.querySelectorAll('[data-test-grouped-position-entity-metadata-container]');
                    console.log(`  Found ${positionContainers.length} positions under ${companyName}`);
                    
                    positionContainers.forEach((container, posIdx) => {
                        try {
                            const dl = container.querySelector('dl[data-test-position]');
                            if (!dl) {
                                console.log(`    Position ${posIdx + 1}: No dl element found`);
                                return;
                            }

                            let title = "N/A";
                            let dateRange = "N/A";
                            let duration = "N/A";
                            let location = "N/A";
                            let employmentStatus = "N/A";

                            const dtElements = dl.querySelectorAll('dt');
                            const ddElements = dl.querySelectorAll('dd');

                            for (let i = 0; i < dtElements.length; i++) {
                                const dtText = dtElements[i].textContent.trim();
                                const dd = ddElements[i];
                                if (!dd) continue;

                                if (dtText === 'Position title') {
                                    const titleText = dd.querySelector('[data-test-text-highlighter-text-only]');
                                    title = titleText ? titleText.textContent.trim() : dd.textContent.trim();
                                } else if (dtText === 'Position employment status') {
                                    const statusText = dd.querySelector('[data-test-text-highlighter-text-only]');
                                    employmentStatus = statusText ? statusText.textContent.trim() : dd.textContent.trim();
                                } else if (dtText === 'Dates employed and Duration') {
                                    const dateSpan = dd.querySelector('[data-test-grouped-position-entity-date-range]');
                                    const durationSpan = dd.querySelector('[data-test-grouped-position-entity-duration]');
                                    
                                    if (dateSpan) {
                                        const dateText = dateSpan.querySelector('[data-test-text-highlighter-text-only]');
                                        dateRange = dateText ? dateText.textContent.trim() : dateSpan.textContent.trim();
                                    }
                                    
                                    if (durationSpan) {
                                        const durText = durationSpan.querySelector('[data-test-text-highlighter-text-only]');
                                        duration = durText ? durText.textContent.trim() : durationSpan.textContent.trim();
                                    }
                                } else if (dtText === 'Position location') {
                                    const locText = dd.querySelector('[data-test-text-highlighter-text-only]');
                                    location = locText ? locText.textContent.trim() : dd.textContent.trim();
                                }
                            }

                            if (dateRange && dateRange !== "N/A") {
                                const dateParts = dateRange.split(/[-–—]/);
                                if (dateParts.length >= 2) {
                                    const startDate = parseDate(dateParts[0]);
                                    const endDate = parseDate(dateParts[1]);
                                    if (startDate) allDates.push(startDate);
                                    if (endDate) allDates.push(endDate);
                                    if (duration === "N/A" && startDate) {
                                        duration = calculateDuration(startDate, endDate);
                                    }
                                }
                            }

                            if (title !== "N/A") {
                                experiences.push({
                                    title: title,
                                    company: companyName,
                                    duration: duration,
                                    dateRange: dateRange,
                                    location: location,
                                    employmentStatus: employmentStatus,
                                    companyTotalDuration: companyTotalDuration
                                });
                                console.log(`    ✓ Added: ${title} | ${dateRange} | ${duration} | ${employmentStatus}`);
                            }
                        } catch (e) {
                            console.log(`    Error parsing position ${posIdx + 1}:`, e);
                        }
                    });
                } catch (e) {
                    console.log(`Error parsing company ${idx + 1}:`, e);
                }
            });
            
            const standaloneContainers = document.querySelectorAll('[data-test-position-list-container]:not([data-test-group-position-list-container] [data-test-position-list-container])');
            console.log(`Found ${standaloneContainers.length} standalone position containers`);
            
            standaloneContainers.forEach((container, idx) => {
                try {
                    let parent = container.parentElement;
                    let isGrouped = false;
                    while (parent) {
                        if (parent.hasAttribute('data-test-group-position-list-container')) {
                            isGrouped = true;
                            break;
                        }
                        parent = parent.parentElement;
                    }
                    
                    if (isGrouped) {
                        return;
                    }

                    const metadataContainer = container.querySelector('[data-test-position-entity-metadata-container]');
                    if (!metadataContainer) return;

                    const dl = metadataContainer.querySelector('dl');
                    if (!dl) return;

                    let title = "N/A";
                    let company = "N/A";
                    let dateRange = "N/A";
                    let duration = "N/A";
                    let location = "N/A";
                    let employmentStatus = "N/A";

                    const dtElements = dl.querySelectorAll('dt');
                    const ddElements = dl.querySelectorAll('dd');

                    for (let i = 0; i < dtElements.length; i++) {
                        const dtText = dtElements[i].textContent.trim();
                        const dd = ddElements[i];
                        if (!dd) continue;

                        if (dtText === 'Position title') {
                            const titleText = dd.querySelector('[data-test-text-highlighter-text-only]');
                            title = titleText ? titleText.textContent.trim() : dd.textContent.trim();
                        } else if (dtText === 'Company name') {
                            const companyText = dd.querySelector('[data-test-text-highlighter-text-only]');
                            company = companyText ? companyText.textContent.trim() : dd.textContent.trim();
                            company = company.replace(/·.*$/, '').trim();
                        } else if (dtText === 'Position employment status') {
                            const statusText = dd.querySelector('[data-test-text-highlighter-text-only]');
                            employmentStatus = statusText ? statusText.textContent.trim() : dd.textContent.trim();
                        } else if (dtText === 'Dates employed and Duration') {
                            const dateSpan = dd.querySelector('[data-test-position-entity-date-range]');
                            const durationSpan = dd.querySelector('[data-test-position-entity-duration]');
                            
                            if (dateSpan) {
                                const dateText = dateSpan.querySelector('[data-test-text-highlighter-text-only]');
                                dateRange = dateText ? dateText.textContent.trim() : dateSpan.textContent.trim();
                            }
                            
                            if (durationSpan) {
                                const durText = durationSpan.querySelector('[data-test-text-highlighter-text-only]');
                                duration = durText ? durText.textContent.trim() : durationSpan.textContent.trim();
                            }
                        } else if (dtText === 'Position location') {
                            const locText = dd.querySelector('[data-test-text-highlighter-text-only]');
                            location = locText ? locText.textContent.trim() : dd.textContent.trim();
                        }
                    }

                    if (dateRange && dateRange !== "N/A") {
                        const dateParts = dateRange.split(/[-–—]/);
                        if (dateParts.length >= 2) {
                            const startDate = parseDate(dateParts[0]);
                            const endDate = parseDate(dateParts[1]);
                            if (startDate) allDates.push(startDate);
                            if (endDate) allDates.push(endDate);
                            if (duration === "N/A" && startDate) {
                                duration = calculateDuration(startDate, endDate);
                            }
                        }
                    }

                    if (title !== "N/A" || company !== "N/A") {
                        experiences.push({
                            title: title,
                            company: company,
                            duration: duration,
                            dateRange: dateRange,
                            location: location,
                            employmentStatus: employmentStatus,
                            companyTotalDuration: "N/A"
                        });
                        console.log(`  ✓ Standalone: ${title} @ ${company} | ${dateRange} | ${duration} | ${employmentStatus}`);
                    }
                } catch (e) {
                    console.log(`Error parsing standalone position ${idx + 1}:`, e);
                }
            });
            
            let totalExperience = "N/A";
            if (allDates.length > 0) {
                const earliestDate = new Date(Math.min(...allDates));
                const latestDate = new Date(Math.max(...allDates));
                totalExperience = calculateDuration(earliestDate, latestDate);
            }

            console.log(`✅ Total experiences extracted: ${experiences.length}`);
            console.log(`✅ Total experience calculated: ${totalExperience}`);

            return {
                experiences: experiences,
                totalExperience: totalExperience
            };
        }""")

        print(f"✅ Found {len(experience_data.get('experiences', []))} experience entries")
        print(f"✅ Total experience: {experience_data.get('totalExperience', 'N/A')}")

        return experience_data

    except Exception as e:
        print(f"❌ Failed to scrape experience for {profile_url}: {e}")
        return {
            "experiences": [],
            "totalExperience": "N/A"
        }

async def scrape_recruiter_profile(page, profile_url):
    try:
        url = clean_recruiter_profile_url(profile_url)
        print(f"🔍 Navigating to recruiter profile: {url}")
        await page.goto(url, timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(5000)
        
        public_url = await get_public_profile_url(page)
        
        await auto_scroll(page, step=500, max_rounds=10, wait_ms=2000)
        await page.wait_for_timeout(3000)

        basic_data = await page.evaluate(r"""() => {
            const getText = (selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
                }
                return "N/A";
            };

            const name = getText([
                '.artdeco-entity-lockup__title.ember-view',
                'div[id*="ember"].artdeco-entity-lockup__title',
                '.artdeco-entity-lockup__title',
                'h1',
                '.profile-topcard__name',
                '.profile-name'
            ]);
            
            let title = "N/A";
            
            const headlineElement = document.querySelector('span[data-test-row-lockup-headline], span[data-live-test-row-lockup-headline]');
            if (headlineElement) {
                const jobTitleElement = headlineElement.querySelector('em.sh');
                if (jobTitleElement) {
                    const jobTitle = jobTitleElement.textContent.trim();
                    const fullText = headlineElement.textContent.trim();
                    const atIndex = fullText.indexOf('@');
                    
                    if (atIndex !== -1) {
                        let companyPart = fullText.substring(atIndex + 1).trim();
                        const separators = ['|', '•', '·', ','];
                        for (const sep of separators) {
                            if (companyPart.includes(sep)) {
                                companyPart = companyPart.split(sep)[0].trim();
                                break;
                            }
                        }
                        title = `${jobTitle} @ ${companyPart}`;
                    } else {
                        title = jobTitle;
                    }
                } else {
                    title = headlineElement.textContent.trim();
                }
            } else {
                title = getText([
                    '.profile-topcard__headline',
                    '.profile-title',
                    '.artdeco-entity-lockup__subtitle'
                ]);
            }
            
            const location = getText([
                'div[data-test-row-lockup-location]',
                'div[data-live-test-row-lockup-location]',
                '.profile-topcard__location',
                '.profile-location'
            ]);

            return {
                name: name !== "N/A" ? name : "Name not found",
                title: title !== "N/A" ? title : "Title not found", 
                location: location !== "N/A" ? location.replace('·', '').trim() : "Location not found"
            };
        }""")

        education_data = await scrape_recruiter_education(page, url)
        experience_data = await scrape_recruiter_experience(page, url)
        skills_data = await scrape_recruiter_skills(page, url)

        experience_details = []
        for exp in (experience_data.get("experiences") or []):
            detail = f"{exp.get('company','N/A')} | {exp.get('title','N/A')} | {exp.get('dateRange','N/A')} | {exp.get('duration','N/A')}"
            experience_details.append(detail)
        experience_details_str = " || ".join(experience_details)

        skills_str = " | ".join(skills_data) if skills_data else "N/A"

        result = {
            "name": basic_data.get("name", "N/A"),
            "title": basic_data.get("title", "N/A"),
            "location": basic_data.get("location", "N/A"),
            "education": education_data,
            "url": public_url if public_url else url,
            "total_experience": experience_data.get("totalExperience", "Limited in Recruiter"),
            "experience_details": experience_details_str if experience_details_str else "Limited in Recruiter",
            "skills": skills_str
        }
        
        print(f"✅ Scraped recruiter profile: {result['name']} - {result['title']}")
        return result

    except Exception as e:
        print(f"❌ Failed to scrape recruiter profile {profile_url}: {e}")
        return {
            "name": "Failed to scrape", 
            "title": "N/A", 
            "location": "N/A",
            "education": "N/A", 
            "url": clean_recruiter_profile_url(profile_url),
            "total_experience": "N/A", 
            "experience_details": "N/A",
            "skills": "N/A"
        }

async def collect_recruiter_profile_urls(page, search_url, limit, role_name):
    profile_urls = set()
    print(f"🔍 Starting to collect {limit} {role_name} profiles from LinkedIn Recruiter: {search_url}")

    try:
        await page.goto(search_url, timeout=90000, wait_until="domcontentloaded")
        await page.wait_for_timeout(10000)
        
        current_url = page.url
        if "/uas/login" in current_url or "/login" in current_url or "/checkpoint" in current_url:
            print("❌ Redirected to login page. LinkedIn Recruiter access required.")
            print("🔑 Please ensure you:")
            print("   1. Are logged in with correct credentials")
            print("   2. Have LinkedIn Recruiter access")
            print("   3. The search URL is valid and accessible")
            return []
            
    except Exception as e:
        print(f"❌ Failed to load search page: {e}")
        print("🔍 This might be due to:")
        print("   - No LinkedIn Recruiter access")
        print("   - Invalid search URL")
        print("   - Network issues")
        return []

    max_attempts = 50
    attempt = 0
    no_new_profiles_count = 0
    
    while attempt < max_attempts and len(profile_urls) < limit:
        attempt += 1
        previous_count = len(profile_urls)
        
        print(f"🔄 Collection attempt {attempt}/{max_attempts} - {role_name} profiles found: {len(profile_urls)}")
        
        print("📜 Scrolling page thoroughly to load all profiles...")
        await auto_scroll(page, step=800, max_rounds=40, wait_ms=2500)
        await page.wait_for_timeout(8000)

        page_content = await page.evaluate(r"""() => {
            return {
                url: window.location.href,
                title: document.title,
                hasProfiles: document.querySelectorAll('a[href*="/talent/profile/"]').length > 0,
                profileCount: document.querySelectorAll('a[href*="/talent/profile/"]').length
            };
        }""")
        print(f"📊 Page debug: {page_content}")

        stable_count = 0
        last_url_count = len(profile_urls)
        
        for collection_round in range(3):
            print(f"🔍 Collection round {collection_round + 1}/3 on current page...")
            
            new_urls = await page.evaluate(r"""() => {
                const profileUrls = [];
                
                const profileLinkSelectors = [
                    'a[href*="/talent/profile/"]',
                    '[data-test-link-to-profile-link="true"]',
                    '[data-live-test-link-to-profile-link="true"]',
                    'a[data-test-link-to-profile-link]',
                    'a[data-live-test-link-to-profile-link]'
                ];
                
                profileLinkSelectors.forEach(selector => {
                    const links = document.querySelectorAll(selector);
                    links.forEach(link => {
                        const href = link.href || link.getAttribute("href") || "";
                        if (href && href.includes("/talent/profile/") && 
                            !href.includes("/company/") &&
                            !href.includes("/school/")) {
                            profileUrls.push(href);
                        }
                    });
                });
                
                const candidateCards = document.querySelectorAll('[data-test-member-card], .candidate-card, .search-result-card');
                candidateCards.forEach(card => {
                    const profileLink = card.querySelector('a[href*="/talent/profile/"]');
                    if (profileLink) {
                        const href = profileLink.href || profileLink.getAttribute("href");
                        if (href) {
                            profileUrls.push(href);
                        }
                    }
                });
                
                const uniqueUrls = [...new Set(profileUrls)];
                console.log(`Found ${uniqueUrls.length} profile URLs on page`);
                return uniqueUrls;
            }""")

            for url in new_urls:
                if url:
                    profile_urls.add(url)
            
            current_url_count = len(profile_urls)
            print(f"   📊 URLs collected this round: {current_url_count - last_url_count}")
            
            if current_url_count == last_url_count:
                stable_count += 1
            else:
                stable_count = 0
                last_url_count = current_url_count
            
            if stable_count >= 2:
                print(f"✅ Collected all {current_url_count - previous_count} profiles from current page!")
                break
            
            await page.evaluate("window.scrollBy(0, 500);")
            await page.wait_for_timeout(3000)
            await page.evaluate("window.scrollBy(0, -200);")
            await page.wait_for_timeout(2000)

        new_profiles_found = len(profile_urls) - previous_count
        print(f"📊 Found {new_profiles_found} new {role_name} profiles. Total profiles: {len(profile_urls)}")

        if new_profiles_found == 0:
            no_new_profiles_count += 1
        else:
            no_new_profiles_count = 0

        if no_new_profiles_count >= 3:
            print("🔄 No new profiles found. Trying different scroll pattern...")
            await page.evaluate(SCROLL_TO_TOP_JS)
            await page.wait_for_timeout(5000)
            await page.evaluate(SCROLL_TO_BOTTOM_JS)
            await page.wait_for_timeout(8000)
            
            try:
                load_more_selectors = [
                    "button:has-text('Show more')",
                    "button:has-text('Load more')",
                    "[data-test-load-more]",
                    ".artdeco-button--secondary"
                ]
                for selector in load_more_selectors:
                    load_more_btn = await page.query_selector(selector)
                    if load_more_btn and await load_more_btn.is_visible():
                        print("🔄 Clicking 'Show more' button...")
                        await load_more_btn.click()
                        await page.wait_for_timeout(8000)
                        break
            except Exception:
                pass
                
            no_new_profiles_count = 0

        if len(profile_urls) >= limit:
            print(f"✅ Collected enough {role_name} profiles: {len(profile_urls)}")
            break

        clicked_next = False
        try:
            next_button_selectors = [
                'button[aria-label="Next"]',
                'button[aria-label="Go to next page"]',
                'a[data-test-pagination-next]',
                'a[data-live-test-pagination-next]',
                'a.pagination__quick-link--next',
                'a[rel="next"]',
                'a[aria-label*="Go to next page"]',
                'button.artdeco-pagination__button--next'
            ]
            
            for selector in next_button_selectors:
                try:
                    next_element = await page.query_selector(selector)
                    if next_element:
                        if await next_element.is_disabled():
                            print(f"ℹ️ Next button found but is disabled ('{selector}').")
                            continue

                        if await next_element.is_visible():
                            print(f"➡️ Found next button ('{selector}'). Moving to next page...")
                            await next_element.click()
                            await page.wait_for_load_state("domcontentloaded", timeout=20000)
                            await page.wait_for_timeout(random.randint(8000, 12000))
                            clicked_next = True
                            print("✅ Moved to next page successfully.")
                            break
                except Exception as e:
                    print(f"⚠️ Could not use selector '{selector}': {e}")
                    continue
            
            if not clicked_next:
                print("🤷 Could not find or click a 'Next' button.")

        except Exception as e:
            print(f"❌ Next button navigation failed: {e}")

        await delay(5000 + random.randint(3000, 6000))

    final_list = list(profile_urls)[:limit]
    print(f"🎯 Final collection: {len(final_list)} {role_name} profiles")
    
    return final_list

async def main():
    async with async_playwright() as p:
        browser, context, page = await setup_browser(p)

        print("📝 LinkedIn Recruiter Profile Scraper")
        print("📝 Please provide the LinkedIn Recruiter talent search URL")
        print("Example: https://www.linkedin.com/talent/search?searchContextId=...&searchKeyword=...")
        
        search_url = ask_question("🔗 Enter the LinkedIn Recruiter search results URL: ").strip()
        if not search_url:
            print("❌ URL is required. Exiting.")
            await browser.close()
            return

        if not validate_recruiter_url(search_url):
            print("❌ Please provide a valid LinkedIn Recruiter talent search URL")
            print("Make sure the URL contains '/talent/search'")
            await browser.close()
            return

        role_name = extract_role_from_recruiter_url(search_url)
        print(f"🎯 Detected role: {role_name}")

        try:
            limit = int(ask_question(f"🔢 How many {role_name} profiles to scrape? (default: 10): ").strip() or "10")
        except Exception:
            limit = 10

        print(f"🎯 Target URL: {search_url}")

        print("🔍 Testing access to search URL...")
        try:
            test_response = await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            
            current_url = page.url
            
            if "/talent/search" not in current_url:
                print(f"⚠️ Redirected to: {current_url}")
                print("🔄 Attempting to navigate back to search URL...")
                await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                current_url = page.url
            
            if "/uas/login" in current_url or "/login" in current_url:
                print("❌ Search URL requires authentication or Recruiter access.")
                print("🔑 Options:")
                print("   1. Make sure you're logged in with the correct LinkedIn account")
                print("   2. Verify you have LinkedIn Recruiter access")
                print("   3. Try logging in manually in the browser")
                
                ask_question("Press Enter to try manual login, or Ctrl+C to exit...")
                
                await page.goto("https://www.linkedin.com/login", timeout=60000)
                print("👉 Please log in manually in the browser window...")
                ask_question("🔑 Press Enter after successful login...")
                
                cookies = await context.cookies()
                cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                print("💾 New login session saved!")
                
                await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(7000)
                
                if "/uas/login" in page.url or "/login" in page.url:
                    print("❌ Still unable to access the search URL.")
                    print("This usually means you don't have LinkedIn Recruiter access.")
                    await browser.close()
                    return
                    
            print("✅ Search URL accessible!")
                
        except Exception as e:
            print(f"❌ Failed to access search URL: {e}")
            await browser.close()
            return

        urls = await collect_recruiter_profile_urls(page, search_url, limit, role_name)
        
        if not urls:
            print(f"❌ No {role_name} profile URLs found.")
            print("🔍 This could be due to:")
            print("   - No search results on the page")
            print("   - Different page structure than expected") 
            print("   - Access restrictions")
            print("\n🔧 Debugging info:")
            
            try:
                page_title = await page.title()
                current_url = page.url
                print(f"   - Current page title: {page_title}")
                print(f"   - Current URL: {current_url}")
                
                profile_elements = await page.evaluate("""() => {
                    const profiles = document.querySelectorAll('a[href*="/in/"], a[href*="/talent/profile/"]');
                    return profiles.length;
                }""")
                print(f"   - Profile-like elements found: {profile_elements}")
                
            except Exception as debug_e:
                print(f"   - Debug info error: {debug_e}")
                
            await browser.close()
            return

        output_file = initialize_csv(f"Recruiter_{role_name}")
        
        print(f"🎯 Starting to scrape {len(urls)} {role_name} profiles from Recruiter...")
        scraped_count = 0
        
        for i, url in enumerate(urls, 1):
            print(f"\n🔍 [{i}/{len(urls)}] Scraping {role_name} recruiter profile: {url}")
            try:
                profile_data = await scrape_recruiter_profile(page, url)
                
                append_to_csv(output_file, profile_data)
                scraped_count += 1
                
                if i < len(urls):
                    delay_time = 4000 + random.randint(2000, 4000)
                    print(f"⏳ Waiting {delay_time/1000:.1f}s before next profile...")
                    await delay(delay_time)
                    
            except Exception as e:
                print(f"❌ Failed to scrape recruiter profile {url}: {e}")
                failed_profile = {
                    "name": "Failed to scrape", 
                    "title": "N/A", 
                    "location": "N/A",
                    "education": "N/A", 
                    "url": url,
                    "total_experience": "N/A", 
                    "experience_details": "N/A",
                    "skills": "N/A"
                }
                append_to_csv(output_file, failed_profile)

        if scraped_count > 0:
            print(f"\n🎉 LinkedIn Recruiter {role_name} Profile Scraping completed!")
            print(f"📊 Total {role_name} profiles scraped: {scraped_count}")
            print(f"📁 Results saved to: {output_file}")
            print(f"📊 Opening Excel file...")
            open_excel(output_file)
        else:
            print("❌ No data was scraped.")

        await browser.close()

if __name__ == "__main__":
    print("🚀 LinkedIn Recruiter Dynamic Profile Scraper")
    print("=" * 70)
    print("📝 This script works with LinkedIn Recruiter talent search URLs")
    print("📝 Example: https://www.linkedin.com/talent/search?searchContextId=...&searchKeyword=...")
    print("📝 Requirements:")
    print("   - LinkedIn Recruiter account access")
    print("   - Valid talent search URL from LinkedIn Recruiter")
    print("   - Proper login credentials")
    print("📝 Make sure to apply your desired filters in Recruiter first, then copy the URL")
    print("=" * 70)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Scraping interrupted by user.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

    print("\n👋 Thanks for using the LinkedIn Recruiter Profile scraper!")