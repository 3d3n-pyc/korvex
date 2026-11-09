//! One-pass HTML escaping of `& < > " '`.

use std::borrow::Cow;

/// The entity for a byte that needs escaping, if any.
#[inline]
fn entity(b: u8) -> Option<&'static str> {
    match b {
        b'&' => Some("&amp;"),
        b'<' => Some("&lt;"),
        b'>' => Some("&gt;"),
        b'"' => Some("&#34;"),
        b'\'' => Some("&#39;"),
        _ => None,
    }
}

/// Escape `s`; borrows it unchanged when nothing needs escaping.
pub fn escape(s: &str) -> Cow<'_, str> {
    let bytes = s.as_bytes();
    let Some(first) = bytes.iter().position(|&b| entity(b).is_some()) else {
        return Cow::Borrowed(s);
    };
    let mut out = String::with_capacity(s.len() + s.len() / 4 + 8);
    out.push_str(&s[..first]);
    let mut start = first;
    for (i, &b) in bytes.iter().enumerate().skip(first) {
        if let Some(e) = entity(b) {
            out.push_str(&s[start..i]);
            out.push_str(e);
            start = i + 1;
        }
    }
    out.push_str(&s[start..]);
    Cow::Owned(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_is_borrowed() {
        assert!(matches!(escape("hello é world"), Cow::Borrowed(_)));
        assert!(matches!(escape(""), Cow::Borrowed(_)));
    }

    #[test]
    fn escapes_all_five() {
        assert_eq!(
            escape("<b>\"T\" & 'J'</b>"),
            "&lt;b&gt;&#34;T&#34; &amp; &#39;J&#39;&lt;/b&gt;"
        );
    }

    #[test]
    fn keeps_unicode() {
        assert_eq!(escape("é<ü>"), "é&lt;ü&gt;");
        assert_eq!(escape("&"), "&amp;");
    }
}
