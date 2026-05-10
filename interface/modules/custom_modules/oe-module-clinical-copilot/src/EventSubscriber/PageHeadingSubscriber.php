<?php

/**
 * Adds two icons to the OemrUI page-heading action row on patient-context
 * pages, both prepended LEFT of the existing expand/contract + help icons:
 *
 *   1. **Open Modern Dashboard** — out-link to the React dashboard for
 *      the same patient. Translates the session `pid` to the FHIR Patient
 *      `uuid` (the React app's URL identifier) by reading `patient_data.uuid`
 *      and stringifying via `UuidRegistry::uuidToString`. Opens in a new
 *      tab so the legacy session is preserved.
 *
 *   2. **Toggle Clinical Co-Pilot** — toggles the chat drawer. Drawer
 *      state lives in the *top* window (chart-bootstrap.js owns it), but
 *      most chart pages render inside iframes, so this PHP-rendered icon
 *      is wired up on the JS side via chart-bootstrap.js. Click delegates
 *      to `top.OE_COPILOT.toggle()`; visual state (outline vs filled) syncs
 *      from the top window's `html.oe-copilot-open` class.
 *
 * Filtering: only inject when a patient is selected in the session, so
 * neither icon appears on settings/admin pages where they'd be meaningless.
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
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Events\UserInterface\BaseActionButtonHelper;
use OpenEMR\Events\UserInterface\PageHeadingRenderEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

final class PageHeadingSubscriber implements EventSubscriberInterface
{
    /**
     * Default URL prefix for the Modern Dashboard out-link, assuming the
     * co-located deployment shape documented in PATIENT_DASHBOARD_MIGRATION.md
     * §13: the React bundle lives at `/patient-dashboard/` under OpenEMR's
     * docroot, and the React Router patient route is `/dashboard/{uuid}`.
     *
     * For local dev environments where the dashboard runs on the Vite dev
     * server (default `http://localhost:5173`) the prefix can be overridden
     * via the `OE_DASHBOARD_BASE_URL` environment variable, e.g.
     * `OE_DASHBOARD_BASE_URL=http://localhost:5173/dashboard/`. No globals
     * setting is exposed because the override is a deploy-shape detail,
     * not a per-installation runtime knob.
     */
    private const DEFAULT_DASHBOARD_BASE_URL = '/patient-dashboard/dashboard/';

    public static function getSubscribedEvents(): array
    {
        return [
            PageHeadingRenderEvent::EVENT_PAGE_HEADING_RENDER => ['onPageHeadingRender', 100],
        ];
    }

    public function onPageHeadingRender(PageHeadingRenderEvent $event): PageHeadingRenderEvent
    {
        if (!$this->shouldInject()) {
            return $event;
        }

        $actions = $event->getActions();

        // 1. Co-Pilot toggle (prepended first — ends up at index 1 after
        //    the modern-dashboard button is prepended below).
        $copilotBtn = new BaseActionButtonHelper([
            'id' => 'oe-copilot-icon',
            'title' => xl('Toggle Clinical Co-Pilot'),
            // Default visual is outline ("not showing"). chart-bootstrap.js
            // swaps `far` ↔ `fas` and toggles `is-active` on the anchor
            // when the drawer opens/closes in the top window.
            'iconClass' => 'far fa-fw fa-lg fa-comment-dots',
            'href' => '#',
            'attributes' => [
                'aria-hidden' => 'false',
                'aria-label' => xl('Toggle Clinical Co-Pilot'),
            ],
            'anchorClasses' => [
                'oe-copilot-icon-link',
            ],
        ]);
        array_unshift($actions, $copilotBtn);

        // 2. Open Modern Dashboard (prepended second — ends up at index 0,
        //    leftmost in the action row). Skipped if the UUID lookup fails.
        $modernDashboardUrl = $this->dashboardUrlForCurrentPatient();
        if ($modernDashboardUrl !== null) {
            $modernBtn = new BaseActionButtonHelper([
                'id' => 'oe-copilot-modern-dashboard',
                'title' => xl('Open in Modern Dashboard (new tab)'),
                'iconClass' => 'fas fa-fw fa-lg fa-th-large',
                'href' => $modernDashboardUrl,
                'attributes' => [
                    'target' => '_blank',
                    // `noopener` denies the new tab a back-reference to
                    // the OpenEMR window, removing a tabnabbing vector.
                    'rel' => 'noopener noreferrer',
                    'aria-label' => xl('Open this patient in the modern dashboard (new tab)'),
                ],
                'anchorClasses' => [
                    'oe-copilot-modern-dashboard-link',
                ],
            ]);
            array_unshift($actions, $modernBtn);
        }

        $event->setActions($actions);

        return $event;
    }

    /**
     * Only render on pages where a patient is selected — both icons are
     * patient-scoped. Outside chart context (admin, calendar without a
     * patient, settings) they'd be no-ops and add visual clutter.
     */
    private function shouldInject(): bool
    {
        try {
            $session = SessionWrapperFactory::getInstance()->getActiveSession();
            $pid = $session->get('pid');
            return is_numeric($pid) && (int) $pid > 0;
        } catch (\Throwable) {
            return false;
        }
    }

    /**
     * Build the Modern Dashboard URL for the patient currently in session,
     * or null if no UUID is registered for them. Same SQL pattern the
     * `chat-panel.php` view uses to look up patient data by pid.
     *
     * The React app's URL pattern is `/dashboard/{uuid}` where `{uuid}` is
     * the FHIR Patient resource id (a stringified UUID). OpenEMR keeps the
     * mapping in `patient_data.uuid` (binary bytes); we stringify via
     * `UuidRegistry::uuidToString`.
     */
    private function dashboardUrlForCurrentPatient(): ?string
    {
        try {
            $session = SessionWrapperFactory::getInstance()->getActiveSession();
            $pid = $session->get('pid');
            if (!is_numeric($pid) || (int) $pid <= 0) {
                return null;
            }

            $row = sqlQuery(
                "SELECT uuid FROM patient_data WHERE pid = ?",
                [(int) $pid]
            );
            if (empty($row) || empty($row['uuid'])) {
                return null;
            }

            $uuidString = UuidRegistry::uuidToString($row['uuid']);
            if (!is_string($uuidString) || $uuidString === '') {
                return null;
            }

            $base = getenv('OE_DASHBOARD_BASE_URL');
            if (!is_string($base) || $base === '') {
                $base = self::DEFAULT_DASHBOARD_BASE_URL;
            }

            return $base . rawurlencode($uuidString);
        } catch (\Throwable) {
            return null;
        }
    }
}
