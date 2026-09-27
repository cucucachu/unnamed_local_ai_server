import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { ImageViewer } from '../ImageViewer';

function render(element: React.ReactElement): ReactTestRenderer {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(element);
  });
  return renderer;
}

/** `testID` is forwarded down `Image`'s composite -> host boundary, so a
 * bare `findAllByProps({ testID })` matches BOTH nodes (same "composite vs.
 * host" duplicate-match shape as `FileList.test.tsx`'s own `findRow`
 * helper, whose docstring has the full explanation) — `onLoad` only
 * survives on the actual `Image` element we rendered, so filtering on it
 * (rather than `onPress` there) uniquely picks that one out here. */
function findImage(renderer: ReactTestRenderer) {
  const candidates = renderer.root.findAllByProps({ testID: 'image-viewer-image' });
  const image = candidates.find((node) => typeof node.props.onLoad === 'function');
  if (!image) throw new Error('no Image element found for testID "image-viewer-image"');
  return image;
}

describe('ImageViewer', () => {
  it('renders the image and a loading spinner before onLoad fires', () => {
    const renderer = render(createElement(ImageViewer, { path: 'photo.png' }));

    expect(findImage(renderer)).toBeTruthy();
    expect(renderer.root.findAllByProps({ testID: 'image-viewer-spinner' }).length).toBeGreaterThan(0);
  });

  it('hides the spinner once the image reports onLoad', () => {
    const renderer = render(createElement(ImageViewer, { path: 'photo.png' }));

    act(() => {
      findImage(renderer).props.onLoad();
    });

    expect(renderer.root.findAllByProps({ testID: 'image-viewer-spinner' })).toHaveLength(0);
    expect(findImage(renderer)).toBeTruthy();
  });

  it('falls back to an error message (not a broken image) on onError', () => {
    const renderer = render(createElement(ImageViewer, { path: 'photo.png' }));

    act(() => {
      findImage(renderer).props.onError();
    });

    expect(renderer.root.findAllByProps({ testID: 'image-viewer-image' })).toHaveLength(0);
    expect(renderer.root.findAllByProps({ testID: 'image-viewer-spinner' })).toHaveLength(0);
  });
});
