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
     * Append the Clinical Co-Pilot menu entry to the patient chart tabs.
     *
     * The patient id is appended by OpenEMR's menu rendering layer when
     * needed, but we also include `pid` so the chat panel can derive the
     * patient context defensively. The authoritative `pid` is still the
     * one held in the active OpenEMR session.
     */
    public function onPatientMenuUpdate(PatientMenuEvent $event): PatientMenuEvent
    {
        $existingMenu = $event->getMenu();

        $menuItem = new \stdClass();
        $menuItem->label = 'Clinical Co-Pilot';
        $menuItem->menu_id = 'mod_clinical_copilot';
        $menuItem->target = 'mod';
        $menuItem->url = OEGlobalsBag::getInstance()->getWebRoot()
            . '/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat-panel.php';

        $existingMenu[] = $menuItem;

        $event->setMenu($existingMenu);

        return $event;
    }
}
