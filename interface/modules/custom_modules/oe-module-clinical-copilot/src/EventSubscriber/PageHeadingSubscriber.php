<?php

/**
 * Adds the Co-Pilot toggle icon to the OemrUI page-heading action row
 * (left of the existing expand/contract + help icons) on patient-context
 * pages.
 *
 * The drawer state lives in the *top* window (chart-bootstrap.js owns it),
 * but most chart pages render inside iframes — so this PHP-rendered icon
 * is wired up on the JS side via chart-bootstrap.js. Click delegates to
 * `top.OE_COPILOT.toggle()`; visual state (outline vs filled) syncs from
 * the top window's `html.oe-copilot-open` class.
 *
 * Filtering: only inject when a patient is selected in the session, so
 * the icon doesn't appear on settings/admin pages where the drawer is
 * meaningless.
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
use OpenEMR\Events\UserInterface\BaseActionButtonHelper;
use OpenEMR\Events\UserInterface\PageHeadingRenderEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

final class PageHeadingSubscriber implements EventSubscriberInterface
{
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

        $button = new BaseActionButtonHelper([
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

        // Prepend so we end up to the LEFT of expand/contract + help.
        $actions = $event->getActions();
        array_unshift($actions, $button);
        $event->setActions($actions);

        return $event;
    }

    /**
     * Only render the icon on pages where a patient is selected — the
     * drawer is only meaningful in chart context. Outside chart context
     * (admin, calendar without a patient, settings) the icon would be a
     * no-op and just adds visual clutter.
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
}
