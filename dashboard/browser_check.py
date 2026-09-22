"""Browser acceptance checks against a running local dashboard, no remote API calls."""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT = Path('reports/dashboard')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:8765',wait_until='networkidle')
        page.evaluate('localStorage.clear()')
        page.reload(wait_until='networkidle')
        page.get_by_role('heading',name='Upcoming games',exact=True).wait_for()
        assert page.locator('html').get_attribute('data-theme')=='dark'
        assert page.get_by_role('button',name='Use light mode',exact=True).count()==1
        assert page.locator('.game-card').count()>0
        assert page.locator('svg.lucide').count()>10
        assert page.locator('img').evaluate_all('(images)=>images.every(i=>i.complete&&i.naturalWidth>0)')
        page.screenshot(path=str(OUTPUT/'games-desktop.png'),full_page=True)
        page.get_by_role('button',name='Use light mode',exact=True).click()
        assert page.locator('html').get_attribute('data-theme')=='light'
        page.get_by_role('button',name='Use dark mode',exact=True).click()
        page.get_by_role('button',name='Table view',exact=True).click()
        assert page.locator('.game-table .game-row').count()>0
        page.screenshot(path=str(OUTPUT/'games-table-desktop.png'),full_page=True)
        page.get_by_role('button',name='Card view',exact=True).click()
        page.get_by_label('Search teams',exact=True).fill('Boston')
        assert page.locator('.game-card').count()>0
        page.get_by_role('button',name='Matchup details').first.click()
        page.get_by_role('heading',name='What moved this forecast').wait_for()
        page.locator('#game-chart').wait_for()
        assert page.locator('.dialog-body').evaluate('(d)=>d.scrollWidth<=d.clientWidth+1')
        assert page.locator('.matchup-hero').evaluate('(d)=>d.children.length===3')
        assert page.locator('#game-chart').evaluate('(c)=>c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)')
        assert page.locator('#matchup-elo-chart').evaluate('(c)=>c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)')
        page.locator('.dialog-body [data-feature-help]').first.click()
        explanation=page.locator('.dialog-body .feature-explanation')
        assert explanation.count()==1
        assert explanation.evaluate('(e)=>e.closest("dialog")!==null')
        assert explanation.evaluate('(e)=>getComputedStyle(e).backgroundColor!=="rgb(255, 255, 255)"')
        page.screenshot(path=str(OUTPUT/'matchup-desktop.png'),full_page=True)
        page.get_by_role('button',name='Close details',exact=True).click()
        page.screenshot(path=str(OUTPUT/'games-dark-desktop.png'),full_page=True)
        page.locator('nav a[data-view="teams"]').click()
        page.locator('#comparison-chart').wait_for()
        page.wait_for_function('() => document.querySelectorAll("#comparison-legend .legend span").length >= 5')
        assert page.locator('#comparison-chart').evaluate('(c)=>c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)')
        assert page.locator('#comparison-legend .legend span').count()>=5
        page.get_by_role('button',name='View BOS',exact=True).click()
        page.get_by_role('heading',name='Elo history',exact=True).wait_for()
        assert page.locator('#elo-chart').evaluate('(c)=>c.getContext("2d").getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)')
        page.screenshot(path=str(OUTPUT/'elo-team-desktop.png'),full_page=True)
        page.keyboard.press('Escape')
        for view in ['performance','history','model','system']:
            page.locator(f'nav a[data-view="{view}"]').click()
            page.wait_for_function('() => !document.querySelector("main .loading")')
            page.screenshot(path=str(OUTPUT/f'{view}-desktop.png'),full_page=True)
            assert page.locator('h1').count()==1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('nav a[data-view="performance"]').click()
        page.wait_for_function('() => !document.querySelector("main .loading")')
        page.locator('[data-strategy="model_value"]').click()
        page.get_by_role('heading',name='Model value (EV > 3%)',exact=True).wait_for()
        page.keyboard.press('Escape')
        page.locator('nav a[data-view="model"]').click()
        page.get_by_role('button',name='Research archive',exact=True).click()
        page.wait_for_function('() => document.querySelectorAll("main tbody tr").length>20')
        for width in [390,768]:
            page.set_viewport_size({'width':width,'height':844})
            page.goto('http://127.0.0.1:8765/#games',wait_until='networkidle')
            page.get_by_role('heading',name='Upcoming games',exact=True).wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'Overflow at {width}'
            page.screenshot(path=str(OUTPUT/f'games-{width}.png'),full_page=True)
            page.screenshot(path=str(OUTPUT/f'games-{width}-viewport.png'))
            page.get_by_role('button',name='Matchup details').first.click()
            page.get_by_role('heading',name='What moved this forecast').wait_for()
            page.screenshot(path=str(OUTPUT/f'matchup-{width}.png'),full_page=True)
            assert page.locator('dialog').evaluate('(d)=>d.getBoundingClientRect().right<=innerWidth')
            page.keyboard.press('Escape')
        # Synthetic statuses exercise colors/filtering only, never written to the ledger.
        snapshot=page.request.get('http://127.0.0.1:8765/api/overview').json()
        snapshot['games']=snapshot['games'][:3]
        for game,status in zip(snapshot['games'],['qualifies','no_bet','pending']):
            game['signal'].update(status=status,label={'qualifies':'Meets criteria','no_bet':'No bet','pending':'Pending'}[status],reason='Browser test fixture only')
        snapshot['games'][0]['probability']=.70
        snapshot['games'][0]['odds'].update(home_decimal=2.20,away_decimal=1.70)
        page.route('**/api/overview*',lambda route:route.fulfill(json=snapshot))
        page.set_viewport_size({'width':390,'height':844})
        page.reload(wait_until='networkidle')
        page.get_by_label('Search teams',exact=True).fill('')
        assert page.locator('.game-card.qualifies').count()==1
        assert page.locator('.game-card.no_bet').count()==1
        page.get_by_role('button',name='Policy qualifies',exact=True).click()
        assert page.locator('.game-card').count()==1
        assert page.locator('.game-card.qualifies').count()==1
        page.get_by_role('button',name='No bet',exact=True).click()
        assert page.locator('.game-card.no_bet').count()==1
        page.get_by_role('button',name='All games',exact=True).click()
        page.get_by_role('button',name='Model pick / underdog',exact=True).click()
        assert page.locator('.game-card').count()>=1
        assert page.locator('.game-card .team-side.picked').count()>=1
        page.get_by_role('button',name='All games',exact=True).click()
        page.get_by_label('Bookmaker',exact=True).select_option('fanduel')
        page.wait_for_function('() => !document.getElementById("refresh").disabled')
        assert page.get_by_label('Bookmaker',exact=True).input_value()=='fanduel'
        page.get_by_role('button',name='Matchup details').first.click()
        page.get_by_role('heading',name='All forecast versions',exact=True).wait_for()
        with page.expect_download() as download:
            page.get_by_role('button',name='Export forecast snapshots',exact=True).click()
        assert download.value.suggested_filename.endswith('-forecasts.csv')
        page.keyboard.press('Escape')
        assert not errors,errors
        browser.close()
        print('Browser checks passed: six views, search, modal, research, logos, charts, desktop, tablet and mobile. No JavaScript errors.')


if __name__=='__main__':
    main()
