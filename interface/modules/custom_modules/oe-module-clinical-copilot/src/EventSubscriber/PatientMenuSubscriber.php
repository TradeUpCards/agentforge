<?php

/**
 * Patient menu subscriber for the Clinical Co-Pilot module.
 *
 * Listens for the `PatientMenuEvent::MENU_UPDATE` event fired when the
 * patient chart's tab menu is being constructed and appends a single
 * "Clinical Co-Pilot" entry that opens the chat panel.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\EventSubscriber;

use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Menu\PatientMenuEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

final class PatientMenuSubscriber implements EventSubscriberInterface
{
    public static function getSubscribedEvents(): array
    {
        return [
            PatientMenuEvent::MENU_UPDATE => 'onPatientMenuUpdate',
        ];
    }

    /**
     * No-op: the patient-menu trigger is intentionally not registered.
     *
     * Earlier iterations injected a "Clinical Co-Pilot" menu item with a
     * `#oe-copilot-open` hash URL. That broke because the patient menu
     * renders inside an iframe — clicking the menu item changed the
     * *iframe's* URL hash, not the top window's, so the top-window
     * `hashchange` listener (in chart-bootstrap.js) never fired.
     *
     * The drawer is now reachable via two top-window-scoped affordances:
     *   - persistent chevron handle on the right edge of the viewport
     *   - floating "Co-Pilot" launcher in the bottom-right corner
     *
     * Both are wired through chart-bootstrap.js / ScriptFilterSubscriber
     * and work identically across all chart tabs (Dashboard, Reports,
     * History, etc.).
     *
     * The subscriber is kept registered for the architecture trace —
     * `PatientMenuEvent::MENU_UPDATE` is a documented integration point;
     * a future iteration could re-introduce a menu item that calls
     * `top.OE_COPILOT.open()` via a JS bridge.
     */
    public function onPatientMenuUpdate(PatientMenuEvent $event): PatientMenuEvent
    {
        // Intentionally empty — see class docblock.
        return $event;
    }
}
