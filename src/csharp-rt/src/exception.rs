//! Managed exception payloads for translated C# `throw` and `catch`.

use std::panic::{catch_unwind, panic_any, resume_unwind, AssertUnwindSafe};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CsException {
    type_name: String,
    message: String,
}

impl CsException {
    pub fn new(type_name: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            type_name: type_name.into(),
            message: message.into(),
        }
    }

    pub fn type_name(&self) -> &str {
        &self.type_name
    }

    pub fn message(&self) -> &str {
        &self.message
    }
}

pub fn throw(exception: CsException) -> ! {
    panic_any(exception);
}

pub fn catch<T>(body: impl FnOnce() -> T) -> Result<T, CsException> {
    match catch_unwind(AssertUnwindSafe(body)) {
        Ok(value) => Ok(value),
        Err(payload) => match payload.downcast::<CsException>() {
            Ok(exception) => Err(*exception),
            Err(payload) => resume_unwind(payload),
        },
    }
}

pub fn is_type(exception: &CsException, type_name: &str) -> bool {
    // Every payload created by this module represents a managed exception, so
    // C# `catch (System.Exception)` matches it regardless of its concrete
    // type. More-specific custom base chains are emitted from corpus type facts
    // before their catch shapes are enabled.
    type_name == "System.Exception" || exception.type_name == type_name
}

pub fn rethrow(exception: CsException) -> ! {
    throw(exception);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catches_managed_exception_with_its_type_and_message() {
        let result = catch(|| {
            throw(CsException::new(
                "System.ArgumentException",
                "value is invalid",
            ))
        });

        let exception = result.unwrap_err();
        assert!(is_type(&exception, "System.ArgumentException"));
        assert_eq!(exception.message(), "value is invalid");
    }

    #[test]
    fn normal_return_does_not_become_an_exception() {
        assert_eq!(catch(|| 42), Ok(42));
    }

    #[test]
    fn catches_managed_exception_after_mutating_csharp_local() {
        let mut value = 0;
        let result = catch(|| {
            value = 100;
            throw(CsException::new(
                "System.ArgumentException",
                "value is invalid",
            ));
        });

        assert!(result.is_err());
        assert_eq!(value, 100);
    }

    #[test]
    fn every_managed_exception_matches_system_exception() {
        let exception = CsException::new(
            "System.ArgumentException",
            "value is invalid",
        );
        assert!(is_type(&exception, "System.Exception"));
    }

    #[test]
    fn rethrow_preserves_the_managed_payload() {
        let result = catch(|| {
            let exception = catch(|| {
                throw(CsException::new(
                    "System.InvalidOperationException",
                    "bad state",
                ))
            })
            .unwrap_err();
            rethrow(exception);
        });

        let exception = result.unwrap_err();
        assert!(is_type(&exception, "System.InvalidOperationException"));
        assert_eq!(exception.message(), "bad state");
    }

    #[test]
    #[should_panic(expected = "native panic")]
    fn non_managed_panics_are_not_caught_as_csharp_exceptions() {
        let _ = catch(|| panic!("native panic"));
    }
}
