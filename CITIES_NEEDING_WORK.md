# Cities Needing Custom Scrapers or Investigation

Last updated: 2026-04-06

## PRIORITY 1: Major Cities Without Working Data Sources

| City | Pop | Issue | Portal Type | URL |
|------|-----|-------|-------------|-----|
| **Houston, TX** | 2.3M | No public API - only aggregated monthly data | Liferay | permits.houstontx.gov |
| **Jacksonville, FL** | 950K | Config points to VA Beach data (WRONG ORG). No COJ ArcGIS permits exist. | JaxEPICS (Angular) | jaxepics.coj.net |
| **Fresno, CA** | 540K | Accela portal requires login | Accela | aca-prod.accela.com/FRESNO |
| **Long Beach, CA** | 470K | No API - Liferay portal with web search only | Liferay | permitslicenses.longbeach.gov |
| **Tulsa, OK** | 410K | All APIs dead - Tyler EnerGov web-only | Tyler EnerGov | tulsaok-energovweb.tylerhost.net |

## PRIORITY 2: Cities With Potentially Fixable Issues

| City | Pop | Issue | Action Needed |
|------|-----|-------|---------------|
| **Jacksonville, FL** | 950K | ArcGIS returns VA data | Find correct COJ ArcGIS endpoint |
| **Phoenix, AZ** | 1.6M | Config exists, marked NO_DATA | Verify ArcGIS endpoint works (tested OK 2026-04-06) |

## Platforms Requiring Browser Automation

These cities use proprietary portals without public APIs:

1. **Liferay Portals** (Houston, Long Beach)
   - Would need Playwright-based scraper
   - Navigate search forms, extract HTML tables

2. **JaxEPICS** (Jacksonville)
   - Custom Angular application
   - May have undocumented API endpoints

3. **Tyler EnerGov** (Tulsa)
   - Web-only interface
   - 1000 record export cap
   - May need multiple date-range queries

4. **Accela with Login** (Fresno)
   - Could potentially work if we can get public access
   - Check if there's a public search mode

## Research Notes

### Houston (permits.houstontx.gov)
- Platform: Liferay DXP
- No individual permit records available via open data
- data.houstontx.gov only has monthly summary stats
- GIS services have no permit layers

### Jacksonville (jaxepics.coj.net)
- Current ArcGIS config points to wrong source
- Need to find COJ's actual permit ArcGIS service
- Or build JaxEPICS scraper

### Phoenix (maps.phoenix.gov)
- ArcGIS endpoint CONFIRMED WORKING 2026-04-06
- Returns fresh permit data with company names
- Config exists - should work after production sync

## Next Steps

1. [ ] Verify Phoenix works in production collection
2. [ ] Find correct Jacksonville ArcGIS endpoint
3. [ ] Research Houston scraping feasibility
4. [ ] Research Long Beach scraping feasibility
5. [ ] Research Tulsa Tyler EnerGov API
