<?php

/**
 * Injects the chart-bootstrap JS + CSS into OpenEMR page heads so the
 * Co-Pilot drawer + floating button are available on patient chart pages.
 *
 * Hooks into:
 *   - ScriptFilterEvent (`html.head.script.filter`) — adds chart-bootstrap.js
 *   - StyleFilterEvent  (`html.head.style.filter`)  — adds chart-bootstrap.css
 *
 * Both files are filtered through `ModulesApplication::filterSafeLocalModuleFiles`
 * which only allows real local files under the modules root.
 *
 * Auth gate: only inject when an authenticated user session exists. The
 * login page (and any other unauthenticated entrypoint) therefore does
 * not download the module's JS/CSS — avoids disclosing the module's
 * existence + behavior to anonymous visitors. chart-bootstrap.js itself
 * already short-circuits on `/login_screen|\/oauth2/` paths, but
 * preventing the asset download in the first place is the cleaner
 * defense.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\EventSubscriber;

use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\Core\ScriptFilterEvent;
use OpenEMR\Events\Core\StyleFilterEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

final class ScriptFilterSubscriber implements EventSubscriberInterface
{
    private const MODULE_PUBLIC_PATH =
        '/interface/modules/custom_modules/oe-module-clinical-copilot/public';

    public static function getSubscribedEvents(): array
    {
        return [
            ScriptFilterEvent::EVENT_NAME => 'onScriptFilter',
            StyleFilterEvent::EVENT_NAME => 'onStyleFilter',
        ];
    }

    public function onScriptFilter(ScriptFilterEvent $event): void
    {
        if (!$this->isAuthenticated()) {
            return;
        }
        $scripts = $event->getScripts();

        // Co-Pilot drawer + floating launcher (original).
        $scripts[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/chart-bootstrap.js'
            . $this->mtimeQuery('/chart-bootstrap.js');

        // HITL extraction-quality banner for the Documents module preview pane.
        // Injected on every authenticated page; the script self-suppresses when
        // the Documents module preview area is not present, and stays silent
        // (no banner, no console error) for pre-P3 documents that have no
        // co_pilot_extracted_fields rows (GET returns 404).
        $scripts[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/hitl-banner.js'
            . $this->mtimeQuery('/hitl-banner.js');

        // PDF.js bundle (vendored under public/vendor/pdfjs/) — required by
        // citation-sidecar.js to render the cited PDF page with bbox overlay.
        // Stays silent on pages where the sidecar never opens.
        $scripts[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/vendor/pdfjs/pdf.min.js'
            . $this->mtimeQuery('/vendor/pdfjs/pdf.min.js');

        // Citation-source sidecar (PRD §5 visual PDF bounding-box overlay).
        // Lives in the top frame.  Listens for the
        // 'oe-copilot/show-citation-source' postMessage from chat-panel.js;
        // renders the cited page in PDF.js with a green SVG bbox overlay.
        // Self-suppresses on pages without an active citation click.
        $scripts[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/citation-sidecar.js'
            . $this->mtimeQuery('/citation-sidecar.js');

        $event->setScripts($scripts);
    }

    public function onStyleFilter(StyleFilterEvent $event): void
    {
        if (!$this->isAuthenticated()) {
            return;
        }
        $styles = $event->getStyles();
        $styles[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/chart-bootstrap.css'
            . $this->mtimeQuery('/chart-bootstrap.css');
        $styles[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/citation-sidecar.css'
            . $this->mtimeQuery('/citation-sidecar.css');
        $event->setStyles($styles);
    }

    /**
     * True iff the request has a valid OpenEMR session with an
     * authenticated user. Mirrors the pid-gate pattern used by
     * `PageHeadingSubscriber::shouldInject()`. Wraps in a try/catch
     * because session access can throw on truly bare entrypoints
     * (no session bootstrapped at all).
     */
    private function isAuthenticated(): bool
    {
        try {
            $session = SessionWrapperFactory::getInstance()->getActiveSession();
            $userId = $session->get('authUserID');
            return is_numeric($userId) && (int) $userId > 0;
        } catch (\Throwable) {
            return false;
        }
    }

    /**
     * Return a `?_t=<mtime>` query string so dev edits to module
     * JS/CSS aren't masked by browser caching. OpenEMR's Header
     * also appends `&v={v_js_includes}`, but that's tied to the OpenEMR
     * version and doesn't change on local module edits.
     */
    private function mtimeQuery(string $publicRelativePath): string
    {
        $abs = __DIR__ . '/../../public' . $publicRelativePath;
        $mtime = @filemtime($abs);
        return $mtime ? ('?_t=' . $mtime) : '';
    }
}
